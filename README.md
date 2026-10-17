# wamove

See [Compatibility and validation](COMPATIBILITY.md) for Windows setup,
conversion coverage, group migration checks, and remaining limits.

Move WhatsApp chats and media from an Android phone to an iPhone that is **already set up**, without erasing the iPhone.

WhatsApp's own Android → iPhone transfer only works while setting up a new or erased iPhone, through the Move to iOS app. If the iPhone is already in use, the official answer is to reset it. wamove works around that by editing WhatsApp's data inside an iPhone backup on your Mac and restoring only that data.

> [!WARNING]
> This uses undocumented formats and is not supported by WhatsApp or Apple. Keep the Android phone and its backup until you are happy with the result. If something goes wrong, `wamove rollback` puts back WhatsApp's data as it was before, and a full iPhone backup stays on your Mac.
>
> The restore makes the iPhone go through its setup screens. Afterwards the home screen layout may be reset and some apps, WhatsApp included, may show faded icons until they are downloaded again. Their data stays.

## How it works

1. `pull` copies the Android phone's encrypted chat backup (`msgstore.db.crypt15`), its media folders and contact names over USB with `adb`.
2. `decrypt` decrypts the backup with its 64-digit end-to-end encryption key.
3. `backup` takes a full backup of the iPhone, then keeps a copy with only WhatsApp's data in it.
4. `build` adds the Android chats and media to WhatsApp's iPhone database (`ChatStorage.sqlite`) in that copy. It learns the conventions from the rows WhatsApp itself wrote on the iPhone, so the new rows look like WhatsApp's own.
5. `restore` restores the WhatsApp-only copy to the iPhone. The backup manifest targets WhatsApp's shared app data, but the iOS restore procedure still restarts the phone and can reopen setup screens or change the home-screen layout.

Nothing is uploaded anywhere. Both phones talk to the Mac over USB.

## Requirements

The installation steps below are for macOS. For Windows, use the
[Python setup and device prerequisites](COMPATIBILITY.md#installation-and-offline-checks).

- A Mac with the Xcode Command Line Tools (`xcode-select --install`)
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- `adb` (`brew install --cask android-platform-tools`)
- A USB cable for each phone
- Free disk space for a full iPhone backup plus the Android media. `wamove doctor` shows both.
- WhatsApp (not WhatsApp Business) on both phones, with the same phone number

## Install

```sh
git clone https://github.com/alicin/wamove.git
./wamove/install.sh
```

or, without cloning:

```sh
uv tool install --python 3.13 git+https://github.com/alicin/wamove.git
```

## Moving your chats

### 1. On the Android phone

1. In WhatsApp, open Settings → Chats → Chat backup → End-to-end encrypted backup, turn it on and choose **Use 64-digit encryption key instead**. Save the key.
2. Tap **Back up**. A backup made before the key was set cannot be decrypted with it.
3. Turn on USB debugging (Settings → Developer options), plug the phone into the Mac and allow it.

```sh
wamove doctor     # checks both phones and free space
wamove pull       # the chat backup and all media
wamove decrypt    # asks for the 64-digit key
```

### 2. On the iPhone

1. Install WhatsApp and register it with the same number. This signs WhatsApp out on the Android phone, which is expected. Skip restoring from iCloud.
2. Send and receive a message and a photo, so the database has rows to learn from.
3. Open WhatsApp once more, so any message it received in the background is saved to its database. Messages that only exist as notifications are lost by the restore.
4. Turn on Airplane mode and turn off Wi-Fi, so no new messages arrive until the restore is done. They wait on WhatsApp's servers and arrive afterwards.
5. Turn off Find My (Settings → your name → Find My). Restores are refused while it is on. Turning it off may need Airplane mode off for a moment: open WhatsApp again before switching Airplane mode back on.

Plug the iPhone into the Mac, unlock it and trust the Mac.

```sh
wamove backup     # full backup, then a WhatsApp-only copy; asks for the backup password if backups are encrypted
wamove inspect    # optional: what is on each side
wamove build      # adds the Android chats to the WhatsApp-only copy
wamove restore    # refuses conversions with reported omissions by default
```

Review `ios/build/report.json` after the build. If you accept its skipped messages,
reduced representations, missing media, or unconverted features, run
`wamove restore --accept-partial`. This acknowledges those omissions; it does not
establish that every feature will display correctly in WhatsApp.

Commands refuse to overwrite existing Android data, iPhone backups, or build
outputs. Keep the previous workspace and use a separate `--work DIR` for another
attempt. Do not delete the only recovery copy to rerun a command.

### 3. After the restore

Finish the setup screens, reinstall WhatsApp from the App Store if its icon is faded, open it, then turn Airplane mode off and Find My back on.

If the result is not right, `wamove rollback` restores the untouched WhatsApp-only copy.

When you are happy, delete `~/wamove`. It holds your decrypted chats.

## Commands

| Command | What it does |
| --- | --- |
| `doctor` | Shows the connected Android phone and iPhone, Find My, backup encryption, free space, and which steps are done |
| `pull [--no-media]` | Copies the chat backup, contact names and media off the Android phone |
| `decrypt [--key-file FILE]` | Decrypts the chat backup and prints how many chats and messages it has |
| `backup` | Takes a full iPhone backup and makes the WhatsApp-only copy |
| `audit [--exclude-groups]` | Reports conversion coverage from decrypted Android data without contacting either phone |
| `inspect` | Shows the Android chats and what is in the iPhone's WhatsApp database |
| `build [--no-media] [--exclude-groups]` | Writes the Android chats (and media) into a new WhatsApp-only copy and records checks and coverage |
| `restore [--accept-partial]` | Restores the validated copy to its matching iPhone; explicit acceptance is required for reported omissions |
| `rollback` | Restores the untouched WhatsApp-only copy to the iPhone |

Options that go before the command:

| Option | |
| --- | --- |
| `--work DIR` | Where everything is kept, default `~/wamove` (or `$WAMOVE_WORK`) |
| `--udid UDID` | Which iPhone to use when several are connected |
| `--contacts FILE.vcf` | Extra contact names for chats, on top of the ones read from the Android phone |

The key and the backup password are asked for in the terminal, or read from `WAMOVE_KEY` and `WAMOVE_BACKUP_PASSWORD`.

The work folder looks like this:

```
~/wamove/
  android/          chat backup, decrypted msgstore.db, contacts.json, Media/
  ios/full/         the full iPhone backup
  ios/pristine/     WhatsApp-only copy, as it was on the iPhone (used by rollback)
  ios/patched/      WhatsApp-only copy with the Android chats added (used by restore)
```

## What moves

Text messages, photos, videos, voice notes, audio, documents, GIFs, stickers, locations and contact cards, with their original dates. Chats that already exist on the iPhone are merged, and messages that are already there are skipped.

Not moved: system messages ("X joined"), deleted-message markers, call logs, reactions, polls, status updates and channels.

## Known issues

Tested once, in September 2026, from a Pixel 10 Pro XL to an iPhone on iOS 27.0: 64 one-to-one chats with about 25,000 messages and 889 media files came across, with thumbnails, and media opened.

- In that earlier test, **group history** was written to the database but did not show up in WhatsApp. A later migration with the mapping fixes in this revision produced an initial user report that group history appeared. This is not confirmation across all versions or a complete check of authors, dates, and attachments; see [validation limits](COMPATIBILITY.md#validation).
- WhatsApp changes its database between versions. If `build` fails its checks, WhatsApp on the iPhone has probably changed something wamove does not know about yet. Please open an issue with the output of `wamove inspect`.

## Troubleshooting

- **`adb` sees no device:** unlock the Android phone and accept the USB debugging prompt. `adb devices` should list it as `device`, not `unauthorized`.
- **No WhatsApp backup found on Android:** wamove looks in `Android/media/com.whatsapp/WhatsApp`. Make a backup in WhatsApp first.
- **`decrypt` fails:** the backup must be made after the 64-digit key was set. Make a new backup on the Android phone, then run `pull` again.
- **The restore is refused:** turn off Find My on the iPhone and run `wamove restore` again.
- **WhatsApp is not in the iPhone backup:** install and register WhatsApp on the iPhone, then run `backup` again.

## Development

```sh
uv run --group dev pytest
```

The tests build small Android and iPhone databases and run the conversion end to end, no phones needed.

## Credits

wamove started from [wabridge](https://github.com/parvesh-rm/wabridge) by Parvesh Kumar (GPL-3.0-or-later), whose ChatStorage writer, crypt15 reader and msgstore parser it adapts. Changes made in September 2026: a WhatsApp-only restore instead of a full one, encrypted backup support, corrected media paths, LID-aware group members and a trimmed command line. It also builds on:

- [green2blue](https://github.com/discordwell/green2blue) (MIT) for how new files are added to an encrypted backup
- [Jalquin's migration notes](https://github.com/Jalquin/whatsapp-android-ios-migration-notes) for the WhatsApp-only restore and the media path layout
- [oath](https://github.com/rohancodesss/oath) for the backup record rules
- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) for talking to the iPhone

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
