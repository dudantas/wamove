from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from fixtures import ALICE, BOB_PN, GROUP, PASSWORD, UDID, make_backup, make_chatstorage, make_media, make_msgstore
from wamove.android import msgstore
from wamove.cli import main
from wamove.ios import backup, chatstorage, device, thumbs
from wamove.model import Archive, Chat, Message


def test_clone_portable_and_preserves_source(tmp_path):
    source, target = tmp_path / "original", tmp_path / "copy"
    source.mkdir()
    (source / "data").write_bytes(b"original")
    backup.clone_tree(source, target)
    (target / "data").write_bytes(b"modified")
    assert (source / "data").read_bytes() == b"original"
    with pytest.raises(backup.BackupError, match="overwrite"):
        backup.clone_tree(source, target)
    assert (target / "data").read_bytes() == b"modified"


def test_clone_rejects_nested_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(backup.BackupError, match="outside"):
        backup.clone_tree(source, source / "nested")


@pytest.mark.parametrize("outcome", ["success", "failure", "partial"])
def test_macos_clone_retains_partial_output(tmp_path, monkeypatch, outcome):
    source, target = tmp_path / "source", tmp_path / "copy"
    source.mkdir()
    (source / "data").write_bytes(b"original")
    monkeypatch.setattr(backup, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(shutil, "which", lambda command: "cp" if command == "cp" else None)

    def fake_copy(command, **kwargs):
        assert command == ["cp", "-Rc", str(source), str(target)]
        if outcome == "success":
            shutil.copytree(source, target)
        elif outcome == "partial":
            target.mkdir()
            (target / "data").write_bytes(b"partial")
        return subprocess.CompletedProcess(command, 0 if outcome == "success" else 1)

    monkeypatch.setattr(subprocess, "run", fake_copy)
    if outcome == "partial":
        with pytest.raises(backup.BackupError, match="partial copy retained"):
            backup.clone_tree(source, target)
        assert (target / "data").read_bytes() == b"partial"
    else:
        backup.clone_tree(source, target)
        assert (target / "data").read_bytes() == b"original"
    assert (source / "data").read_bytes() == b"original"


def test_parser_accounts_for_every_row(tmp_path):
    database = tmp_path / "msgstore.db"
    make_msgstore(database)
    parsed = msgstore.parse(database)
    assert parsed.source_messages == 19
    assert parsed.stats()["messages"] + sum(parsed.skipped.values()) == 19
    assert parsed.skipped == {"system": 2, "revoked": 1, "excluded_chat": 1,
                             "unsupported_type_99": 1, "empty_text": 1}
    assert parsed.downgraded_types == {49: 1}
    individual = msgstore.parse(database, exclude_groups=True)
    assert all(not chat.is_group for chat in individual.chats)
    assert individual.skipped["excluded_group"] == 3
    assert individual.stats()["messages"] + sum(individual.skipped.values()) == 19


def test_audit_is_offline_and_preserves_database(tmp_path, capsys):
    android = tmp_path / "android"
    android.mkdir()
    make_msgstore(android / "msgstore.db")
    before = (android / "msgstore.db").read_bytes()
    assert main(["--work", str(tmp_path), "audit", "--exclude-groups"]) == 0
    assert "0 groups" in capsys.readouterr().out
    assert (android / "msgstore.db").read_bytes() == before
    assert not (tmp_path / "ios").exists()


def test_portuguese_and_emoji_contacts(tmp_path, capsys):
    android = tmp_path / "android"
    android.mkdir()
    make_msgstore(android / "msgstore.db")
    (android / "contacts.json").write_text(json.dumps({"61400000002": "João 💙"}, ensure_ascii=False), encoding="utf-8")
    assert main(["--work", str(tmp_path), "audit"]) == 0


def test_thumbnail_alpha_and_corrupt_input(tmp_path):
    source, target = tmp_path / "sticker.webp", tmp_path / "thumbnail.thumb"
    Image.new("RGBA", (90, 120), (255, 0, 0, 127)).save(source)
    assert thumbs.image(str(source), target) == (90, 120)
    assert thumbs.dimensions(str(target)) == (72, 72)
    source.write_bytes(b"invalid image")
    assert thumbs.image(str(source), target) is None
    assert not target.exists()


def test_video_without_ffmpeg_is_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert thumbs.video(str(tmp_path / "video.mp4"), tmp_path / "video.thumb") is False


def test_media_lookup_preserves_relative_identity_and_rejects_escape(tmp_path):
    root = tmp_path / "Media"
    (root / "Pictures").mkdir(parents=True)
    (root / "Sent").mkdir()
    first, second = root / "Pictures" / "same.jpg", root / "Sent" / "same.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    chatstorage._media_index.cache_clear()
    assert chatstorage._locate("/storage/emulated/0/Android/media/com.whatsapp/WhatsApp/Media/Pictures/same.jpg", str(root)) == str(first)
    assert chatstorage._locate("Media/Sent/same.jpg", str(root)) == str(second)
    assert chatstorage._locate("unavailable/same.jpg", str(root)) is None
    (tmp_path / "secret.jpg").write_bytes(b"outside")
    assert chatstorage._locate("../secret.jpg", str(root)) is None
    assert chatstorage._locate("C:/private/secret.jpg", str(root)) is None


def test_groups_do_not_deduplicate_different_senders(tmp_path):
    database = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(database)
    archive = Archive([Chat(GROUP, is_group=True, messages=[
        Message("shared-id", False, 1726000000000, text="Alice", sender_jid=ALICE),
        Message("shared-id", False, 1726000001000, text="Bob", sender_jid=BOB_PN)])])
    writer = chatstorage.Writer(database)
    try:
        report = writer.import_archive(archive, None)
        assert report.messages_written == 2
        assert writer.import_archive(archive, None).messages_written == 0
    finally:
        writer.close()
    assert chatstorage.verify(database) == []


def test_group_sender_failure_rolls_back_entire_import(tmp_path):
    database = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(database)
    archive = Archive([
        Chat(ALICE, messages=[Message("new", True, 1726000000000, text="test")]),
        Chat(GROUP, is_group=True, messages=[Message("missing", False, 1726000001000, text="no author")])])
    writer = chatstorage.Writer(database)
    try:
        with pytest.raises(chatstorage.ChatStorageError, match="no sender"):
            writer.import_archive(archive, None)
    finally:
        writer.close()
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ZWAMESSAGE").fetchone()[0] == 1


def test_group_verification_detects_wrong_chat_member(tmp_path):
    database = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(database)
    writer = chatstorage.Writer(database)
    writer.import_archive(Archive([Chat(GROUP, is_group=True, messages=[
        Message("incoming", False, 1726000000000, text="hello", sender_jid=BOB_PN)])]), None)
    writer.close()
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE ZWAGROUPMEMBER SET ZCHATSESSION=1")
    assert any("mismatched sender" in p for p in chatstorage.verify(database))


def make_work(root):
    (root / "android").mkdir(parents=True)
    make_msgstore(root / "android" / "msgstore.db")
    make_media(root / "android" / "Media")
    make_chatstorage(root / "ChatStorage.sqlite")
    make_backup(root / "ios" / "pristine" / UDID, root / "ChatStorage.sqlite", PASSWORD)


def test_build_report_and_repeat_preserve_output(tmp_path, monkeypatch):
    make_work(tmp_path)
    monkeypatch.setenv("WAMOVE_BACKUP_PASSWORD", PASSWORD)
    assert main(["--work", str(tmp_path), "build", "--exclude-groups"]) == 0
    report = json.loads((tmp_path / "ios" / "build" / "report.json").read_text())
    assert report["completed"] and report["selected"]["groups"] == 0
    assert report["skipped"]["excluded_group"] == 3
    assert report["downgraded_types"] == {"49": 1}
    target = tmp_path / "ios" / "patched" / UDID / "Manifest.db"
    before = target.read_bytes()
    assert main(["--work", str(tmp_path), "build"]) == 1
    assert target.read_bytes() == before
    # A conversion with omissions cannot silently reach a real device.
    assert main(["--work", str(tmp_path), "restore"]) == 1


def test_failed_build_cannot_be_restored(tmp_path, monkeypatch):
    make_work(tmp_path)
    monkeypatch.setenv("WAMOVE_BACKUP_PASSWORD", PASSWORD)
    def broken(*args, **kwargs):
        raise chatstorage.ChatStorageError("simulated unsupported schema")
    monkeypatch.setattr(chatstorage.Writer, "import_archive", broken)
    assert main(["--work", str(tmp_path), "build"]) == 1
    result = json.loads((tmp_path / "ios" / "build" / "report.json").read_text())
    assert not result["completed"]
    assert main(["--work", str(tmp_path), "restore", "--accept-partial"]) == 1


def test_inspect_releases_windows_database_handle(tmp_path, monkeypatch):
    make_work(tmp_path)
    monkeypatch.setenv("WAMOVE_BACKUP_PASSWORD", PASSWORD)
    assert main(["--work", str(tmp_path), "inspect"]) == 0


def test_related_features_are_reported(tmp_path):
    database = tmp_path / "msgstore.db"
    make_msgstore(database)
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE message_add_on (message_row_id INTEGER)")
        conn.execute("INSERT INTO message_add_on VALUES (100)")
        conn.execute("CREATE TABLE message_quoted (message_row_id INTEGER)")
        conn.execute("INSERT INTO message_quoted VALUES (101)")
    parsed = msgstore.parse(database)
    assert parsed.unconverted_tables == {"message_add_on": 1, "message_quoted": 1}
    assert parsed.stats()["messages"] + sum(parsed.skipped.values()) == parsed.source_messages


def test_group_and_private_message_flags_are_learned_separately(tmp_path):
    database = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(database)
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO ZWAMESSAGE (Z_PK,Z_ENT,Z_OPT,ZCHATSESSION,ZISFROMME,ZMESSAGETYPE,ZFLAGS,"
                     "ZSTANZAID,ZMESSAGEDATE) VALUES (2,6,1,2,1,0,12345,'native-group',748000000)")
        conn.execute("UPDATE Z_PRIMARYKEY SET Z_MAX=2 WHERE Z_NAME='WAMessage'")
    writer = chatstorage.Writer(database)
    writer.import_archive(Archive([
        Chat(ALICE, messages=[Message("private", True, 1726000000000, text="one")]),
        Chat(GROUP, is_group=True, messages=[Message("group", True, 1726000001000, text="two")])]), None)
    writer.close()
    with sqlite3.connect(database) as conn:
        flags = dict(conn.execute("SELECT ZSTANZAID,ZFLAGS FROM ZWAMESSAGE WHERE ZSTANZAID IN ('private','group')"))
    assert flags == {"private": 16777280, "group": 12345}


def test_restore_refuses_a_changed_manifest_without_contacting_phone(tmp_path, monkeypatch):
    make_work(tmp_path)
    monkeypatch.setenv("WAMOVE_BACKUP_PASSWORD", PASSWORD)
    assert main(["--work", str(tmp_path), "build"]) == 0
    path = tmp_path / "ios" / "patched" / UDID / "Manifest.db"
    path.write_bytes(path.read_bytes() + b"corrupt")
    assert main(["--work", str(tmp_path), "restore", "--accept-partial"]) == 1


def test_restore_refuses_wrong_selected_phone_offline(tmp_path, monkeypatch):
    make_work(tmp_path)
    monkeypatch.setenv("WAMOVE_BACKUP_PASSWORD", PASSWORD)
    assert main(["--work", str(tmp_path), "build"]) == 0
    assert main(["--work", str(tmp_path), "--udid", "some-other-phone", "restore", "--accept-partial"]) == 1


@pytest.mark.parametrize("connected_id, find_my, expected", [
    (UDID, False, 0), ("other-phone", False, 1), (UDID, True, 1),
])
def test_restore_binds_validated_package_to_target(tmp_path, monkeypatch, connected_id, find_my, expected):
    make_work(tmp_path)
    monkeypatch.setenv("WAMOVE_BACKUP_PASSWORD", PASSWORD)
    assert main(["--work", str(tmp_path), "build"]) == 0
    selected, restored = [], []

    def fake_phone(udid):
        selected.append(udid)
        return SimpleNamespace(udid=connected_id, find_my=find_my)

    monkeypatch.setattr(device, "phone", fake_phone)
    monkeypatch.setattr(device, "restore", lambda *args: restored.append(args))
    assert main(["--work", str(tmp_path), "restore", "--accept-partial"]) == expected
    assert selected == [UDID]
    assert restored == ([(tmp_path / "ios" / "patched", UDID, PASSWORD, UDID)] if expected == 0 else [])


def test_backup_refuses_overwriting_preserved_snapshot_without_contacting_phone(tmp_path):
    snapshot = tmp_path / "ios" / "full"
    snapshot.mkdir(parents=True)
    (snapshot / "original").write_bytes(b"preserved")
    assert main(["--work", str(tmp_path), "backup"]) == 1
    assert (snapshot / "original").read_bytes() == b"preserved"


def test_decrypt_refuses_overwriting_recovered_chats(tmp_path):
    folder = tmp_path / "android" / "Databases"
    folder.mkdir(parents=True)
    (folder / "msgstore.db.crypt15").write_bytes(b"unused")
    recovered = folder.parent / "msgstore.db"
    recovered.write_bytes(b"existing recovery")
    assert main(["--work", str(tmp_path), "decrypt"]) == 1
    assert recovered.read_bytes() == b"existing recovery"


def test_empty_chats_do_not_crash_and_latest_message_is_chronological(tmp_path):
    database = tmp_path / "ChatStorage.sqlite"
    make_chatstorage(database)
    writer = chatstorage.Writer(database)
    report = writer.import_archive(Archive([
        Chat(BOB_PN, messages=[]),
        Chat(ALICE, messages=[Message("later", True, 1736000002000, text="last"),
                              Message("earlier", True, 1736000001000, text="first")])]), None)
    writer.close()
    assert report.messages_written == 2
    with sqlite3.connect(database) as conn:
        text = conn.execute("SELECT m.ZTEXT FROM ZWACHATSESSION s JOIN ZWAMESSAGE m ON m.Z_PK=s.ZLASTMESSAGE "
                            "WHERE s.ZCONTACTJID=?", (ALICE,)).fetchone()[0]
    assert text == "last"
