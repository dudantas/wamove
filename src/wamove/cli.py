from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import __version__
from .android import adb, crypt15, msgstore
from .ios import backup as ios_backup
from .ios import chatstorage, device
from .model import Archive

CHATSTORAGE = ios_backup.CHATSTORAGE


class Failure(Exception):
    pass


class Work:
    def __init__(self, root: Path):
        self.root = root
        self.android = root / "android"
        self.crypt15 = self.android / "Databases" / "msgstore.db.crypt15"
        self.msgstore = self.android / "msgstore.db"
        self.media = self.android / "Media"
        self.contacts = self.android / "contacts.json"
        self.full = root / "ios" / "full"
        self.pristine = root / "ios" / "pristine"
        self.patched = root / "ios" / "patched"
        self.build = root / "ios" / "build"
        self.report = self.build / "report.json"

    def udid(self) -> str:
        found = [p.name for p in self.pristine.iterdir() if (p / "Manifest.db").is_file()] \
            if self.pristine.is_dir() else []
        if not found:
            raise Failure("there is no iPhone backup yet; run `wamove backup` first")
        if len(found) > 1:
            raise Failure(f"{self.pristine} holds more than one iPhone; remove the one you are not moving to")
        return found[0]


def secret(env: str, prompt: str, file: Path | None = None) -> str:
    if file:
        return file.read_text().strip()
    if os.environ.get(env):
        return os.environ[env]
    if not sys.stdin.isatty():
        raise Failure(f"{prompt} needed: run this in a terminal or set {env}")
    return getpass.getpass(f"{prompt}: ")


def backup_password(path: Path) -> str | None:
    if not ios_backup.is_encrypted(path):
        return None
    return secret("WAMOVE_BACKUP_PASSWORD", "iPhone backup password")


def gigabytes(value: int) -> str:
    return f"{value / 1e9:.1f} GB"


def load_archive(work: Work, vcf: Path | None, *, exclude_groups: bool = False) -> Archive:
    if not work.msgstore.is_file():
        raise Failure("the Android chats are not decrypted yet; run `wamove decrypt` first")
    contacts = json.loads(work.contacts.read_text(encoding="utf-8")) if work.contacts.is_file() else {}
    if vcf:
        contacts.update(msgstore.load_vcf(vcf))
    return msgstore.parse(work.msgstore, contacts, exclude_groups=exclude_groups)


def print_stats(archive: Archive) -> None:
    stats = archive.stats()
    print(f"  {stats['chats']} chats ({stats['groups']} groups), {stats['messages']} messages, "
          f"{stats['media']} with media")
    if archive.skipped:
        print("  Not converted: " + json.dumps(archive.skipped, sort_keys=True))
    if archive.downgraded_types:
        print("  Message types converted with reduced information: " + json.dumps(archive.downgraded_types, sort_keys=True))
    if archive.unconverted_tables:
        print("  Related tables not converted: " + json.dumps(archive.unconverted_tables, sort_keys=True))


def audit(work: Work, args: argparse.Namespace) -> None:
    """Read Android data without contacting either phone or writing a backup."""
    archive = load_archive(work, args.contacts, exclude_groups=args.exclude_groups)
    print_stats(archive)
    print(f"  Source message rows: {archive.source_messages}")


def doctor(work: Work, args: argparse.Namespace) -> None:
    try:
        model = adb.device()
        root = adb.whatsapp_root()
        print(f"Android: {model}, WhatsApp backup found in {root}")
    except adb.AdbError as e:
        print(f"Android: {e}")
    try:
        phone = device.phone(args.udid)
        find_my = {True: "on", False: "off", None: "unknown"}[phone.find_my]
        used = gigabytes(phone.used_bytes) if phone.used_bytes else "unknown"
        print(f"iPhone: {phone.name}, iOS {phone.ios}, backups {'encrypted' if phone.encrypts_backups else 'not encrypted'}, "
              f"{used} used, Find My {find_my}")
    except device.DeviceError as e:
        print(f"iPhone: {e}")
    work.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    print(f"Computer: {gigabytes(shutil.disk_usage(work.root).free)} free in {work.root}")
    steps = [
        ("pull", work.crypt15.is_file()),
        ("decrypt", work.msgstore.is_file()),
        ("backup", work.pristine.is_dir() and any(work.pristine.iterdir())),
        ("build", work.patched.is_dir() and any(work.patched.iterdir())),
    ]
    print("Done so far: " + (", ".join(name for name, done in steps if done) or "nothing"))


def pull(work: Work, args: argparse.Namespace) -> None:
    print(f"Android: {adb.device()}")
    root = adb.whatsapp_root()
    if work.crypt15.exists() or work.msgstore.exists() or work.contacts.exists() or work.media.exists():
        raise Failure("this workspace already contains Android data; choose a new --work folder to preserve it")
    work.android.mkdir(mode=0o700, parents=True, exist_ok=True)
    print("Copying the chat backup…")
    adb.pull(f"{root}/Databases/msgstore.db.crypt15", work.crypt15.parent)
    names = adb.contacts()
    work.contacts.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {len(names)} contact numbers saved for naming chats")
    if args.no_media:
        return
    needed = adb.remote_bytes(f"{root}/Media")
    free = shutil.disk_usage(work.root).free
    if needed > free:
        raise Failure(f"the media needs {gigabytes(needed)} but only {gigabytes(free)} is free")
    print(f"Copying {gigabytes(needed)} of media…")
    for folder in adb.media_dirs(root):
        adb.pull(f"{root}/Media/{folder}", work.media)


def decrypt(work: Work, args: argparse.Namespace) -> None:
    if not work.crypt15.is_file():
        raise Failure("there is no Android backup yet; run `wamove pull` first")
    if work.msgstore.exists():
        raise Failure("decrypted chats already exist; use a separate workspace for another attempt")
    key = crypt15.parse_key(secret("WAMOVE_KEY", "64-digit WhatsApp backup key", args.key_file))
    crypt15.decrypt_file(work.crypt15, work.msgstore, key)
    os.chmod(work.msgstore, 0o600)
    print("Decrypted the Android chats:")
    print_stats(load_archive(work, args.contacts))


def backup(work: Work, args: argparse.Namespace) -> None:
    if any(path.exists() for path in (work.full, work.pristine, work.patched)):
        raise Failure("iPhone backup data already exists; choose a new --work folder to preserve it")
    phone = device.phone(args.udid)
    print(f"iPhone: {phone.name}, iOS {phone.ios}")
    work.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    free = shutil.disk_usage(work.root).free
    if phone.used_bytes and phone.used_bytes > free:
        raise Failure(f"the backup needs about {gigabytes(phone.used_bytes)} but only {gigabytes(free)} is free")
    print("Taking a full backup of the iPhone. Keep it unlocked and plugged in; it may ask for its passcode.")
    udid = device.backup(work.full, args.udid)
    full = work.full / udid
    password = backup_password(full)
    ios_backup.clone_tree(full, work.pristine / udid)
    with ios_backup.Backup(work.pristine / udid, password) as saved:
        saved.prune()
        if not saved.get(CHATSTORAGE):
            raise Failure("WhatsApp is not in this backup: install it on the iPhone, register, and back up again")
        count = saved.count()
    print(f"  full backup kept in {full}")
    print(f"  WhatsApp-only copy with {count} entries in {work.pristine / udid}")


def build(work: Work, args: argparse.Namespace) -> None:
    udid = work.udid()
    pristine = work.pristine / udid
    password = backup_password(pristine)
    archive = load_archive(work, args.contacts, exclude_groups=args.exclude_groups)
    print("Android chats:")
    print_stats(archive)
    if work.build.exists() or (work.patched / udid).exists():
        raise Failure("build output already exists; preserve it and use a separate workspace for another attempt")
    work.build.mkdir(parents=True)
    result = {"completed": False, "udid": udid, "source_messages": archive.source_messages,
              "selected": archive.stats(), "skipped": archive.skipped,
              "downgraded_types": archive.downgraded_types, "unconverted_tables": archive.unconverted_tables,
              "device_validated": False, "group_mapping_experimental": bool(archive.stats()["groups"])}
    work.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    ios_backup.clone_tree(pristine, work.patched / udid)
    database = work.build / CHATSTORAGE
    with ios_backup.Backup(work.patched / udid, password) as target:
        for name in (CHATSTORAGE, CHATSTORAGE + "-wal"):
            target.read(name, work.build / name)
        writer = chatstorage.Writer(database, archive.lids)
        media = None if args.no_media or not work.media.is_dir() else work.media
        (work.build / "thumbs").mkdir()
        try:
            report = writer.import_archive(archive, media, work.build / "thumbs")
        finally:
            writer.close()
        problems = chatstorage.verify(database)
        if problems:
            raise Failure("the converted database failed its checks:\n  " + "\n  ".join(problems))
        print(f"  {report.sessions_created} chats created, {report.sessions_reused} merged into existing ones")
        print(f"  {report.messages_written} messages added, {report.messages_skipped} were already there")
        print(f"  {report.media_linked} media files found, {report.media_missing} missing on the Android phone, "
              f"{report.thumbnails} thumbnails made")
        target.put(CHATSTORAGE, database)
        empty = work.build / "empty"
        empty.write_bytes(b"")
        for suffix in ("-wal", "-shm"):
            target.put(CHATSTORAGE + suffix, empty)
        total = len(report.media_files)
        for done, item in enumerate(report.media_files, 1):
            target.put(item.relative_path, Path(item.source))
            if done % 100 == 0 or done == total:
                sys.stdout.write(f"\r  added {done}/{total} media files to the backup")
                sys.stdout.flush()
        if total:
            sys.stdout.write("\n")
        problems = target.check()
        if problems:
            raise Failure("the modified backup failed its checks:\n  " + "\n  ".join(problems[:20]))
    result.update({"completed": True, "messages_written": report.messages_written,
                   "duplicates_skipped": report.messages_skipped, "media_linked": report.media_linked,
                   "media_missing": report.media_missing, "thumbnails": report.thumbnails,
                   "manifest_sha256": hashlib.sha256((work.patched / udid / "Manifest.db").read_bytes()).hexdigest()})
    work.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Offline checks passed: {work.patched / udid}")
    print(f"Coverage report: {work.report}. Display in WhatsApp still needs device validation.")


def inspect(work: Work, args: argparse.Namespace) -> None:
    if work.msgstore.is_file():
        print("Android chats:")
        print_stats(load_archive(work, args.contacts))
    udid = work.udid()
    pristine = work.pristine / udid
    with tempfile.TemporaryDirectory() as scratch:
        saved = ios_backup.Backup(pristine, backup_password(pristine))
        try:
            for name in (CHATSTORAGE, CHATSTORAGE + "-wal"):
                saved.read(name, Path(scratch) / name)
            print(f"iPhone backup: {saved.count()} WhatsApp entries, encrypted: {saved.encrypted}")
        finally:
            saved.close()
        database = Path(scratch) / CHATSTORAGE
        conn = sqlite3.connect(database)
        for table in ("ZWACHATSESSION", "ZWAMESSAGE", "ZWAMEDIAITEM", "ZWAGROUPMEMBER"):
            print(f"  {table}: {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]} rows")
        conn.close()
        writer = chatstorage.Writer(database)
        try:
            conventions = dict(writer.conv)
        finally:
            writer.close()
        conventions.pop("required")
        print("  conventions: " + json.dumps(conventions))


def restore(work: Work, args: argparse.Namespace, source: Path) -> None:
    udid = work.udid()
    if not (source / udid / "Manifest.db").is_file():
        raise Failure(f"nothing to restore in {source}; run `wamove build` first")
    if source == work.patched:
        if not work.report.is_file():
            raise Failure("this build has no validation report; refusing to restore")
        result = json.loads(work.report.read_text(encoding="utf-8"))
        digest = hashlib.sha256((source / udid / "Manifest.db").read_bytes()).hexdigest()
        if not result.get("completed") or result.get("udid") != udid or result.get("manifest_sha256") != digest:
            raise Failure("this build is incomplete or its manifest changed; refusing to restore")
        if (result.get("skipped") or result.get("downgraded_types") or result.get("media_missing")
                or result.get("unconverted_tables")):
            if not args.accept_partial:
                raise Failure(f"the conversion is partial; review {work.report}, then use --accept-partial if intended")
    if args.udid and args.udid != udid:
        raise Failure("the selected iPhone does not match the backup; refusing to restore")
    phone = device.phone(udid)
    if phone.udid != udid:
        raise Failure("the connected iPhone does not match the backup; refusing to restore")
    if phone.find_my:
        raise Failure("turn off Find My on the iPhone first (Settings > your name > Find My), then run this again")
    if phone.find_my is None:
        print("Could not tell whether Find My is on. If the restore fails, turn it off and try again.")
    password = backup_password(source / udid)
    print("Restoring WhatsApp's data to the iPhone. Keep it plugged in; it restarts when done.")
    print("Afterwards it goes through its setup screens. The home screen layout may be reset and some apps,")
    print("WhatsApp included, may need downloading again; their data stays.")
    device.restore(source, udid, password, udid)
    print("Done. Reinstall WhatsApp if its icon is faded, open it, then turn Find My back on.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wamove", description="Move WhatsApp chats from Android to an iPhone.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--work", type=Path, default=Path(os.environ.get("WAMOVE_WORK", Path.home() / "wamove")),
                        help="folder for everything wamove copies and builds (default ~/wamove)")
    parser.add_argument("--udid", help="the iPhone to use when several are connected")
    parser.add_argument("--contacts", type=Path, help="a .vcf export to name chats with")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check both phones and this computer")
    command = commands.add_parser("audit", help="report Android conversion coverage without connecting to phones")
    command.add_argument("--exclude-groups", action="store_true")
    command = commands.add_parser("pull", help="copy the WhatsApp backup and media off the Android phone")
    command.add_argument("--no-media", action="store_true")
    command = commands.add_parser("decrypt", help="decrypt the Android backup with its 64-digit key")
    command.add_argument("--key-file", type=Path)
    commands.add_parser("backup", help="back up the iPhone and keep a WhatsApp-only copy")
    command = commands.add_parser("build", help="add the Android chats to the WhatsApp-only copy")
    command.add_argument("--no-media", action="store_true")
    command.add_argument("--exclude-groups", action="store_true")
    commands.add_parser("inspect", help="show what is in the Android chats and the iPhone backup")
    command = commands.add_parser("restore", help="put the built WhatsApp data on the iPhone")
    command.add_argument("--accept-partial", action="store_true", help="acknowledge omissions in the coverage report")
    commands.add_parser("rollback", help="put the untouched WhatsApp data back on the iPhone")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="  %(message)s")
    work = Work(args.work.expanduser())
    actions = {
        "doctor": lambda: doctor(work, args),
        "audit": lambda: audit(work, args),
        "pull": lambda: pull(work, args),
        "decrypt": lambda: decrypt(work, args),
        "backup": lambda: backup(work, args),
        "build": lambda: build(work, args),
        "inspect": lambda: inspect(work, args),
        "restore": lambda: restore(work, args, work.patched),
        "rollback": lambda: restore(work, args, work.pristine),
    }
    known = (Failure, adb.AdbError, crypt15.Crypt15Error, msgstore.MsgstoreError, ios_backup.BackupError,
             chatstorage.ChatStorageError, device.DeviceError)
    try:
        actions[args.command]()
    except known as e:
        print(f"wamove: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
