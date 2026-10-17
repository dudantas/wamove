# Compatibility and validation

Migration remains experimental. Passing offline tests does not establish that WhatsApp will
display the converted database on a particular iOS/app version.

## Implemented corrections

- Portable backup copies on Windows; macOS retains the optional APFS clone path.
- Image, GIF, and sticker thumbnails use Pillow instead of the macOS-only `sips`.
  Video thumbnails optionally use FFmpeg, with a bounded timeout.
- UTF-8 Android output and contact files, including Portuguese names and emoji.
- Group and individual message flags are learned separately from native rows.
- Group deduplication includes the sender and direction; sender references are
  validated. Invalid group imports roll back the whole database transaction.
- Media lookup preserves relative paths, rejects traversal outside the media
  directory, and refuses ambiguous basename matches.
- Repeated pull/decrypt/backup/build commands refuse to replace existing data.
  Use a separate workspace for a new attempt; retain previous workspaces.
- `audit` reports skipped messages, reduced representations, and recognized
  auxiliary tables that are not converted. It does not contact either phone.
- Builds write `ios/build/report.json`, including conversion coverage and a
  manifest hash. Failed builds, changed manifests, and mismatched iPhone IDs
  cannot proceed through the restore command.
- `restore --accept-partial` is required when the report contains omissions.
  This acknowledges documented omissions; it does not validate compatibility.

## Still unimplemented or unverified

The mapping fixes address specific defects in group message identity, sender
references, and flags. They do not establish compatibility with every iOS or
WhatsApp version; see the validation scope below.

There is no native conversion for reactions, polls, edit history, quoted-reply
relationships, system events, deleted-message markers, call history, statuses,
or channels. Known auxiliary tables are counted in the report; schemas can
introduce new tables, so the report is not a proof of complete feature coverage.
Unknown message types with text can be reduced to text and are reported as such.
Multiple-contact messages currently keep one card; live location keeps a static
coordinate. Group exclusion is available independently of these limitations.

This revision accepts the upstream crypt15 + matching 64-digit key flow. It does
not extract an Android private key or decrypt an old crypt14 backup without its
matching key. Existing recovery copies must remain intact.

Restore is experimental and may restart the iPhone, display setup screens,
change home-screen layout, or require apps to download again. Keep an original
encrypted iPhone backup and the original Android files before any live attempt.

## Installation and offline checks

From the repository root, using Python 3.10 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . pytest
.\.venv\Scripts\python.exe -m wamove --help
```

The device operations also need Android Platform Tools and Apple's Windows
device support. ADB and iPhone discovery are separate from offline conversion.

In a **copy** of a migration workspace containing decrypted Android data:

```powershell
.\.venv\Scripts\python.exe -m wamove --work .\migration-copy audit
.\.venv\Scripts\python.exe -m wamove --work .\migration-copy audit --exclude-groups
```

An offline build additionally requires an original WhatsApp iPhone backup in
the workspace's `ios/pristine/<device-id>` directory. Do not create one by moving
the only original backup. Build writes to new output paths and never connects
to a phone. Review `ios/build/report.json` before considering device validation.

Tests use synthetic Android and iPhone databases. An automatic fixture blocks
network connections, external subprocesses, and device operations. The tests
exercise conversion and backup serialization, not WhatsApp's UI or its servers.

## Validation

On Windows with Python 3.12.14, 36 tests passed on 2026-10-17. CLI help,
`uv lock --check --offline`, and Git whitespace checks also passed. Three focused
group regressions were rerun with the original `chatstorage.py` from commit
`989b268cbfa2e0bc3285a45161eb23dde55ecaeb` in the same synthetic harness: all
three failed there and pass with the corrected writer.

The automated suite uses synthetic data only. It covers encrypted and plain
backup serialization, group sender identity and deduplication, transaction
rollback, portable thumbnails and copies, coverage reports, and restore guards.
The restore tests replace device discovery and transfer with local fakes; the
macOS copy tests replace the external `cp` command. These tests do not contact
phones, launch external commands, or access the network.

On 2026-10-17, a separate migration using this revision from Android to an iPhone
on iOS 26.4.1 completed through the restore service. After the iOS setup screens,
the user reported that conversations, including group history, appeared. This
is initial on-device feedback, not an exhaustive comparison of authors, dates,
and playable attachments or a claim that all versions now work.

That migration included additional local preparation and verification outside
the CLI, including correction of inherited manifest size metadata in a working
copy. It therefore does not establish that the standalone CLI handles every
source backup without additional diagnosis. Private databases, media, keys,
device identifiers, and migration logs are not included in this repository.

## References

- [Upstream known issues](https://github.com/alicin/wamove#known-issues)
- [Migration research notes](https://github.com/Jalquin/whatsapp-android-ios-migration-notes)
- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3)
- [WhatsApp Crypt Tools](https://github.com/ElDavoo/wa-crypt-tools)
