<div align="center">

<img src="icon.svg" width="96" alt="GP Sync">

# GP Sync

### Your photo folders, backed up to Google Photos. Automatically, in original quality.

Point GP Sync at the folders on your server, NAS, Immich library or Umbrel. It sends every new
photo and video to Google Photos, skips what Google already has, and shows you exactly where each
file is: on every account you back up to.

[![Docker Hub](https://img.shields.io/docker/v/kshanxs/gp-sync?sort=semver&logo=docker&logoColor=white&label=Docker%20Hub&color=4862e6)](https://hub.docker.com/r/kshanxs/gp-sync)
[![Release](https://img.shields.io/github/v/release/kshanxs/gp-sync?logo=github&color=4862e6)](https://github.com/kshanxs/gp-sync/releases)
![Platforms](https://img.shields.io/badge/platforms-amd64%20%7C%20arm64-19191b)
[![License: MIT](https://img.shields.io/badge/license-MIT-19191b)](LICENSE)

[**Install on Umbrel**](#install-on-umbrel) &nbsp;·&nbsp; [**Run with Docker**](#run-with-docker) &nbsp;·&nbsp; [**Features**](#what-it-does) &nbsp;·&nbsp; [**Before you start**](#before-you-start) &nbsp;·&nbsp; [**Changelog**](CHANGELOG.md)

<br>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/overview-dark.webp">
  <img src="docs/overview-light.webp" alt="GP Sync dashboard: everything running now, a card per process, speed and status at a glance" width="100%">
</picture>

</div>

<br>

<table>
  <tr>
    <td width="33%" valign="top">
      <b>Set it and forget it</b><br>
      <sub>Checks your folders every 5 minutes and sends new photos and videos in original quality. Half-copied files wait; files already in Google Photos are skipped.</sub>
    </td>
    <td width="33%" valign="top">
      <b>Up to 5 Google accounts</b><br>
      <sub>Back each folder up to one account or several at once, and see per account what was uploaded, what Google already had and what is still to sync.</sub>
    </td>
    <td width="33%" valign="top">
      <b>Read-only by design</b><br>
      <sub>Your folders are mounted read-only. Nothing on your server is ever changed or deleted.</sub>
    </td>
  </tr>
  <tr>
    <td valign="top">
      <b>Free up Google storage</b><br>
      <sub>Moves items that count against your storage to uploads that don't, after checking every copy byte for byte.</sub>
    </td>
    <td valign="top">
      <b>Made for your phone too</b><br>
      <sub>Upload from the gallery or camera, follow progress from the couch, and pick up where you left off if Wi-Fi drops.</sub>
    </td>
    <td valign="top">
      <b>Runs anywhere</b><br>
      <sub>One small Docker image for Linux, Windows, macOS, NAS boxes, Raspberry Pi, Immich setups and Umbrel.</sub>
    </td>
  </tr>
</table>

<div align="center">

<table>
  <tr>
    <td width="33%" align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/phone-overview-dark.webp"><img src="docs/phone-overview-light.webp" alt="GP Sync on a phone: everything running now and a card per process" width="100%"></picture><br><sub>Everything running now</sub></td>
    <td width="33%" align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/phone-sync-dark.webp"><img src="docs/phone-sync-light.webp" alt="GP Sync on a phone: backup progress and the files uploading now" width="100%"></picture><br><sub>Backup progress</sub></td>
    <td width="33%" align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/phone-upload-dark.webp"><img src="docs/phone-upload-light.webp" alt="GP Sync on a phone: upload from the gallery or camera" width="100%"></picture><br><sub>Upload from your phone</sub></td>
  </tr>
</table>

</div>

## Get started

### Install on Umbrel

1. In the Umbrel **App Store**, open **⋯ → Community App Stores**.
2. Add `https://github.com/kshanxs/kshanxs-umbrel-store` and open **kshanx's App**.
3. Install **GP Sync**, then [connect your Google account](#connect-your-google-account) in
   **Settings → Google accounts**.

### Run with Docker

```yaml
services:
  gp-sync:
    image: kshanxs/gp-sync:latest
    restart: unless-stopped
    ports: ["8527:8527"]
    environment:
      TZ: Asia/Kolkata                  # your time zone
      PASSWORD: change-me               # dashboard login; leave out on a trusted network
    volumes:
      - ./gp-sync-data:/data            # app data: accounts, state, statistics
      - /path/to/photos:/media/Photos:ro
```

Run `docker compose up -d`, open `http://<server>:8527` and connect your Google account. Windows,
Immich, every setting and the full example are in [Run it anywhere](#run-it-anywhere).

> [!TIP]
> Let an AI assistant set it up: copy a prompt from [docs/PROMPTS.md](docs/PROMPTS.md) into Claude
> Code or Codex, on a laptop, server, NAS or Umbrel.

## What it does

### Back up

- **Every folder you choose, to the accounts you choose.** Connect up to 5 Google accounts and pick,
  per folder, one account or several at once: the first is the main backup, the others get copies.
  Photos and Videos go to the first account by default (or every folder, if those don't exist).
- **Safe by default.** A file is sent only after it has stopped changing for 2 minutes, files
  already in Google Photos are skipped, and a failed file is retried on later scans, up to 5 times
  (**Retry failed** resets the count).
- **Back up first.** Pin up to 10 folders so their files go before everything else, in the order
  you set.
- **Upload hours, speed limit, files at once.** Only upload between set hours, cap the speed so the
  rest of your internet stays fast, and send several files side by side per account (the speed
  limit is shared). **Dynamic** starts at 4, adds more while Google keeps up (up to 12, and fewer
  for big videos: at most 256 MB moving at once) and halves when Google asks to slow down, putting
  that file back in the queue instead of stopping. Or pick a fixed number, 1 to 8.
- **Albums.** New uploads can go into Google Photos albums named after their folder.
- **Upload from your phone.** Pick, drag or take photos and videos in the dashboard (or share them
  from the Android gallery). They're saved in the upload folder by `<year>/<month>/`, files already
  there are recognised, and everything is backed up like the rest.

### See where every file is

- **Overview.** Everything running now (uploads, freeing storage, copies, date fixes, Snapchat
  memories, library reads) at the top, then a card per process with today and the last 14 days.
  It opens with **what changed since your last visit**: files added, backed up and failed.
- **Sync.** What uploads now and next, the whole library's progress, and a card per account that
  splits its files into **Uploaded** (sent by GP Sync), **Already in Google** (often your phone's
  own backup, so nothing was sent), **New to sync** and **Failed**, with the time left at the pace
  of its last hour. Tap a part to see those files.
- **Folders.** A browser for everything backed up. One tree on the left with each folder's share in
  Google Photos and how long ago files last arrived ("3h", "2d"); the open folder on the right with
  what each account has of it, five numbers that filter its photos, and tabs for **Photos**,
  **Folders inside** and **Added** (arrivals per day). With two or more accounts, one switch makes
  every percentage that account's, so you see which folders it still lacks.
- **Library.** Search and filter everything that was backed up, with thumbnails (iPhone HEIC too)
  and a link to each item in Google Photos. A duplicates tab lists identical files.
- **Activity and Insights.** 90 days of activity grouped by day, with filters, search and
  thumbnails. Per process: totals, streaks, best day, a heat map of the year, busiest hours and
  weekdays, average sizes, file types, the largest and slowest uploads.
- **Network and Health.** Speed gauges, live traffic, Google response time and data used per day;
  CPU, memory, disk, database sizes and a one-click check of every login.

### Free up Google storage

- **Free storage** lists what counts against your Google storage, biggest first, photos and videos
  apart. Free single items, the 10 biggest, or everything of a kind. For each item GP Sync gets it,
  checks it against the checksum Google gives, deletes it in Google Photos (Trash included),
  uploads it again the way that doesn't count and confirms it no longer does. Each step is saved,
  so a restart resumes safely.
- **Automatic.** Turn on "Free new items automatically" and what your phones' Google Photos app
  backs up is freed as it arrives (checked every 15 minutes, in every account).
- **Fast moving.** Before downloading anything, GP Sync looks on this server for a file with the
  same checksum and copies that instead. The original is never moved or changed.
- **Downloads.** Keep the downloaded copies on the server, per account, to save or delete later.
- **Freed by GP Sync** shows what was freed: totals, time taken, per day and each item with a link
  to check it in Google Photos.

### Move between accounts

- **Copy accounts.** Copy chosen items, or everything of a type and year range, from one connected
  account to another: only what the other doesn't have, in original quality, with dates,
  favourites, archive and captions. Nothing is deleted in either account. (Albums and people names
  don't come over: the login cannot list the source's albums.)
- **Keep in sync.** Keep one account copied into another, or both into each other. GP Sync compares
  both libraries straight away and then every hour, and copies only what's missing.

### Labs

Newer tools that work, and may still change how they work.

- **Sources.** Where every photo came from (camera, Snapchat, Instagram, TikTok, WhatsApp, Telegram,
  screenshot or "saved from an app"), with the evidence. Saved items can go into an album per app,
  get a searchable caption or be archived; Snapchat saves get a date estimated from your own camera
  photos.
- **Fix dates.** Photos with no date inside (Snapchat saves, screenshots) that Google dated by the
  upload are replaced with ones uploaded again with the right date, keeping favourite, archive,
  caption and GP Sync albums. Only when the file here is exactly what was uploaded.
- **Snapchat Memories.** Bring in Snapchat's "Download My Data" export with its real dates and
  places. GP Sync finds each memory already in Google Photos first, so no memory ends up there
  twice. [How it works](#snapchat-memories)

### Stay in the loop

- **Alerts** by ntfy or Telegram when Google rejects a login, a file fails 5 times or a backup
  finishes, plus a weekly summary every Monday.
- **App log and diagnostics.** 30 days of searchable log, and one JSON file with everything needed to
  look into a problem (never your Google logins). Also from the [command line](#command-line-and-diagnostics).
- **Your time zone** for every day, hour and chart, daylight saving included (**Settings →
  Appearance**).
- **Settings where you need them.** "How many at once" and its choices open right on the page they
  affect; everything else is in **Settings** (Backup, Google Photos, App), where **Google accounts →
  Manage** checks logins and reads libraries for several accounts at once.

### Designed to stay out of the way

A quiet dashboard: graphite at night, paper by day, so your photos and the backup's progress carry
the colour. Sidebar on desktop; bottom tabs and a **More** sheet on phones. Pages update only what
changed, so lists don't jump and charts keep your hover. Every page starts the same way: what it is
for, a **How it works** where it needs explaining, its numbers, then its filters.

<div align="center">
<table>
  <tr>
    <td width="33%" align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/history-dark.webp"><img src="docs/history-light.webp" alt="History: what was backed up, day by day" width="100%"></picture><br><sub>History</sub></td>
    <td width="33%" align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/insights-dark.webp"><img src="docs/insights-light.webp" alt="Insights: upload times, file sizes and patterns" width="100%"></picture><br><sub>Insights</sub></td>
    <td width="33%" align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/network-dark.webp"><img src="docs/network-light.webp" alt="Network: live speed, Google response time and data used" width="100%"></picture><br><sub>Network</sub></td>
  </tr>
</table>
</div>

## Before you start

> [!WARNING]
> **Free storage depends on Google.** GP Sync uploads the way the Google Photos app does on
> phones whose uploads don't count against storage. If Google stops honouring that, new uploads
> start counting like any other: backups still work, but the storage isn't free. GP Sync checks:
> **Free storage** stops as soon as Google counts one of its re-uploads, **Copy accounts** pauses,
> and nothing is deleted in an account where GP Sync's own uploads count.

- **Unofficial API.** Uploads go through [gpmc](https://github.com/xob0t/google_photos_mobile_client),
  which talks to Google Photos like the Android app. Google could change or block it at any time.
- **The login line is a password.** It gives full access to that account's photos. Keep it only in
  GP Sync; the AI prompts tell assistants not to save it.
- **Free storage and Fix dates delete items in Google Photos** before uploading them again, each only
  after checking the copy here byte for byte against Google's checksum. Quality, date, favourite,
  archive and caption are kept. **Albums made by hand, shared links and comments are lost.** Items
  shared with you, in the Trash or in the Locked folder are never touched.
- **Downloads need space.** Free storage downloads each item here first (it checks the free space),
  unless the same file is already on the server. With "Delete the downloaded copy" off, copies stay
  under **Downloads** until you delete them.
- **iPhone Live Photos** arrive as a photo plus a separate 2–3 second video for now: Google doesn't
  join the halves when they come as separate files. Sending them as one motion photo works (tested)
  and is coming next. Android motion photos (one file) are fine already.
- **Nothing on your server is changed.** Deleting a file there doesn't delete it in Google Photos,
  and GP Sync never deletes files on the server (only its own downloads and thumbnails in `/data`).
  The one writable folder is the upload folder (**Photos/Uploads** on Umbrel).
- **Dates.** Photos with no date inside get the best date GP Sync can find; Google Photos dates
  them by the upload otherwise. Arrival dates on Folders come from filesystem timestamps, which can
  change.
- **No password by default.** Without `PASSWORD`, anyone who can reach the dashboard can use it. Keep
  it on your home network or behind a VPN such as Tailscale.

## FAQ

<details>
<summary><b>Is this made by Google?</b></summary>

No. GP Sync is an independent open-source project. It signs in the way the Google Photos Android
app does, through [gpmc](https://github.com/xob0t/google_photos_mobile_client).
</details>

<details>
<summary><b>Will it delete anything on my server?</b></summary>

No. Your folders are mounted read-only. GP Sync only ever deletes its own downloads and thumbnails
in `/data`.
</details>

<details>
<summary><b>Will it upload a photo twice?</b></summary>

No. Files Google Photos already has are skipped and shown as **Already in Google**, per account.
</details>

<details>
<summary><b>Do uploads count against my Google storage?</b></summary>

GP Sync uploads the way phones whose uploads don't count do. That depends on Google: see
[Before you start](#before-you-start). GP Sync watches for it and stops freeing storage the moment
Google counts one of its uploads.
</details>

<details>
<summary><b>What happens if my server restarts mid-upload?</b></summary>

Nothing is lost. Free storage saves each step and resumes safely; a file whose upload was cut off is
simply sent again on the next scan.
</details>

## Connect your Google account

1. On an Android phone, install [GmsCore](https://github.com/ReVanced/GmsCore/releases) and
   [Google Photos ReVanced](https://github.com/j-hc/revanced-magisk-module/releases).
2. Turn on USB debugging and connect the phone to a computer with `adb`. Open Google Photos
   ReVanced and sign in. If the account is already in GmsCore, remove it there first.
3. Read the sign-in line your phone already wrote to its log, and print only that line:

   ```sh
   # macOS or Linux
   adb logcat -d -b all | grep -o 'androidId=.*' | grep 'auth%2Fphotos.native' | tail -1
   ```

   If it prints nothing, run `adb logcat | grep -m 1 'auth%2Fphotos.native'` and sign in again.
   See [docs/PROMPTS.md](docs/PROMPTS.md#get-the-google-login-line) for the Windows command.
4. Copy the text from `androidId=` to the end of the line, paste it in **Settings → Google accounts**
   and press **Connect**.

Prefer an assistant to run it? Copy the [login-line prompt](docs/PROMPTS.md#get-the-google-login-line)
(also in the app, under **Settings → Google accounts**, with a **Copy prompt** button) into Claude
Code or Codex. It is worded so assistants understand it is your own account on your own device, since
some refuse account-token steps otherwise.

Treat that line like a password: it gives full access to the Google Photos library.

## Run it anywhere

GP Sync is a Docker image for Intel/AMD (amd64) and ARM (arm64): [`kshanxs/gp-sync`](https://hub.docker.com/r/kshanxs/gp-sync)
on Docker Hub (also `ghcr.io/kshanxs/gp-sync`). It runs on Linux servers, NAS boxes, Raspberry Pi,
Windows and macOS with Docker Desktop, or Umbrel. It backs up **whatever folders you mount under
`/media`**, read-only: a phone backup folder, an Immich library, a NAS share, anything. Each folder
shows up in **Settings → Folders**, where you choose which Google accounts back it up. Everything
the app needs is inside the image; nothing is downloaded at start.

### Docker Compose

```yaml
services:
  gp-sync:
    image: kshanxs/gp-sync:latest
    restart: unless-stopped
    ports: ["8527:8527"]
    environment:
      TZ: Asia/Kolkata                  # your time zone
      HOST_NAME: My NAS                 # what the dashboard calls this machine
      PASSWORD: change-me               # dashboard login (any user name); leave out on a trusted network
    volumes:
      - ./gp-sync-data:/data            # app data: accounts, state, statistics
      - /path/to/photos:/media/Photos:ro
      - /path/to/videos:/media/Videos:ro
      - /path/to/photos/Uploads:/uploads   # optional: where the Upload page saves files
```

Run `docker compose up -d`, open `http://<server>:8527` and connect your Google account.
Use a version tag (for example `:1.8.0`) instead of `latest` to update only when you choose.

### Windows (Docker Desktop)

Use Windows paths on the left side; Docker Desktop shares the drive:

```yaml
    volumes:
      - C:\gp-sync-data:/data
      - D:\Pictures:/media/Pictures:ro
      - C:\Users\you\OneDrive\Camera Roll:/media/Camera Roll:ro
```

Or in one line (PowerShell):

```powershell
docker run -d --name gp-sync --restart unless-stopped -p 8527:8527 -v C:\gp-sync-data:/data -v "D:\Pictures:/media/Pictures:ro" kshanxs/gp-sync:latest
```

### Immich

Mount Immich's originals, not its whole upload location (that also holds thumbnails):

```yaml
    volumes:
      - ${UPLOAD_LOCATION}/library:/media/Immich:ro   # with the storage template on
      - ${UPLOAD_LOCATION}/upload:/media/Immich uploads:ro   # originals without the template
```

Immich's `encoded-video` folder is skipped by default. If you do mount the whole upload location,
also skip its other folders: `EXCLUDE: "@eaDir,#recycle,encoded-video,thumbs,profile,backups"`.

### Settings

| Variable | Default | What it does |
|---|---|---|
| `TZ` | `UTC` | The system default time zone for upload hours, history and the weekly summary (Settings → Appearance can choose another; without `TZ`, the dashboard device's zone is used) |
| `HOST_NAME` | `this server` | What the dashboard calls the machine ("Folders on My NAS") |
| `PLATFORM` | *(any Docker host)* | `umbrel` turns on Umbrel's wording and hints (the Umbrel app sets it). Leave it out anywhere else: nothing assumes a platform |
| `PASSWORD` | none | Shows a sign-in page before the dashboard (remembered for a year; works in the installed app). Scripts use `curl -u any:PASSWORD` |
| `ALLOWED_HOSTS` | none | Without `PASSWORD`, the dashboard answers only home-network addresses (IP addresses, `localhost`, one-word names, `.local`, `.lan`, `.home.arpa`, `.internal`, Tailscale's `.ts.net`). Add other names here, comma-separated (`photos.example.com`, or `.example.com` for every name under it); `*` turns the check off. Not needed with `PASSWORD` or on Umbrel |
| `FOLDERS` | `Photos,Videos` | Folders under `/media` backed up until you choose in Settings; if none exist, all are |
| `EXCLUDE` | `@eaDir,#recycle,#snapshot,$RECYCLE.BIN,System Volume Information,lost+found,encoded-video` | Folder and file names never backed up (`*` and `?` allowed). Hidden names are always skipped |
| `SCAN_INTERVAL_MINUTES` | `5` | How often folders are checked |
| `SETTLE_SECONDS` | `120` | A file must be unchanged this long before it's sent (skips half-copied files) |
| `PUID` / `PGID` | `1000` | Owner of files saved from the Upload page |
| `REDIS_URL` | none | Use a Redis or Valkey server as the cache (normally the app's own memory is used) |

| Path in the container | What |
|---|---|
| `/data` | App data (writable): Google logins, upload state, statistics, thumbnails |
| `/media/<name>` | Each folder to back up, read-only. The name is what the dashboard shows |
| `/uploads` | Optional, writable: files sent from the Upload page. Mount a folder that is also inside a backed-up folder so they get backed up too |

Without `PASSWORD`, the dashboard has no login: keep it on your home network or behind a VPN
such as Tailscale. It then answers only home-network addresses and refuses changes sent from other
websites; to open it by a domain name without a password, list the name in `ALLOWED_HOSTS`. The
network speed on the page is this container's own traffic.

## Snapchat Memories

`scripts/snapchat_export.py` turns the zips from Snapchat's "Download My Data" into a folder (for
example `Photos/Snapchat`) and writes Snapchat's exact date, time and place into every photo and
video, with the text and stickers layer burned in:

```bash
# on the server, with exiftool and ffmpeg: every zip from Snapchat in ~/Downloads
python3 scripts/snapchat_export.py ~/Downloads ~/Photos/Snapchat
```

The **Snapchat** page then looks for each memory in Google Photos before anything is uploaded: the
same file, a camera-roll copy taken in the same second, or one that looks the same, and every
further copy. One copy stays (the original, or one with the right date) and goes into the Snapchat
album; when Google only has later-dated copies, the memory is uploaded with Snapchat's date, time
and place in their place. Every copy that goes is deleted from Google: the memory on this server is
the same picture, and a less certain match is downloaded first (Downloads page). Photos from a
camera are never deleted, copies on this server aren't uploaded as well, and files in the export
folder that aren't in its list are held back. Albums you made yourself in Google Photos can't be
read, so they don't carry over. Nothing changes in Google until you press **Start**.

## Command line and diagnostics

The image has a `gpsync` command. It talks to the running app, so it is safe to use at any time:

```sh
docker exec <container> gpsync status                     # what the app is doing
docker exec <container> gpsync diagnostics > diag.json    # one JSON file to share when something's wrong
docker exec <container> gpsync logs --level error         # recent errors and warnings
docker exec <container> gpsync activity --kind google     # what was fixed, freed or copied
docker exec <container> gpsync check-login                # does each Google login still work?
docker exec <container> gpsync refresh you@gmail.com      # read an account's Google library again
docker exec <container> gpsync storage you@gmail.com      # what counts against its storage
docker exec <container> gpsync free you@gmail.com --kind video --keep --yes   # free storage
docker exec <container> gpsync downloads                  # copies kept per account
```

On Umbrel the container is `kshan-gphotos-sync_web_1` (`sudo docker exec …`). Add `--json` to most
commands for JSON. Diagnostics never include Google logins, but they do include file names and
account addresses.

## REST API and statistics

Uploads and network traffic are logged per hour in a [Turso](https://turso.tech) database
(`/data/stats.db`), using Turso's open-source engine (`pyturso`). Tables: `hourly` (per-hour totals),
`events` (activity, 90 days), `uploads` (one row per file with its upload time), `samples`
(per-minute speed, CPU, memory and Google response time, 14 days) and `logs` (app log, 30 days). The
file is SQLite-compatible. Days are built in the viewer's time zone. The upload state itself
(`/data/state.db`) stays in plain SQLite, because Turso is still beta and a statistics problem must
never stop backups; if Turso can't be used, statistics fall back to SQLite automatically.

<details>
<summary><b>Endpoints</b></summary>

| Endpoint | Returns |
|---|---|
| `GET /api/status` | Live status, library progress, current file, speeds, failures, activity |
| `GET /api/history?days=365&tz=330` | Per-day totals; `tz` is minutes east of UTC (India = 330) |
| `GET /api/totals` | All-time totals and fastest upload |
| `GET /api/files?q=&status=all&folder=&sort=new&limit=60&offset=0` | Search backed-up files |
| `GET /api/folders?group=folders&path=Photos&days=30&tz=330` | Counts and files added per day for a folder, or `group=devices` for the folders one level inside each backed-up folder. Each account row has `files`, `done`, `uploaded`, `exists`, `waiting` and `failed`; the folder's own total adds `per_hour` (files done in the last hour) |
| `GET /api/folders/files?path=Photos&status=all&kind=all&sort=new&account=` | A folder's photos and videos with their state; with `account`, only the files that account should get, and `status` can also be `uploaded` or `exists` |
| `GET /api/copy?src=&dst=` | What one account has that another lacks, copying progress, the checker and Keep in sync |
| `POST /api/copy/mirror` | Keep in sync: `{"src", "dst", "mode": "off"\|"one"\|"both"}` |
| `GET /api/storage?account=` | What counts against Google storage, biggest first, progress of moving it, each account's status and the checker |
| `POST /api/storage/download` | Free storage: `{"account", "keys": [...]}` or `{"account", "kind": "photo"\|"video"\|"all"}`, plus `"keep": true` to keep the copies |
| `GET /api/storage/freed?account=` | What Free storage freed: totals, time taken, per day, and each item |
| `POST /api/storage/pause` | Pause or resume Free storage in an account: `{"account", "paused": true}` |
| `GET /api/downloads?account=` | Copies kept after freeing storage, per account |
| `GET /api/snapchat?action=replace&limit=120` | Snapchat memories: the export, the search in Google, what happens to each one |
| `POST /api/snapchat/start` | Start memories: `{"account", "actions": ["album", "replace", "upload"]}` or `{"account", "paths": [...]}` |
| `POST /api/snapchat/action` | Choose for memories: `{"paths": [...], "action": "album"\|"replace"\|"upload"\|"skip"\|"auto"}` |
| `GET /api/diagnostics?hours=24` | Everything needed to look into a problem, as one JSON file (no logins) |
| `GET /api/duplicates` | Groups of identical files |
| `GET /api/health` | Version, storage, logins |
| `GET /api/activity?kind=all&q=&before=&limit=100` | Saved activity (all, uploaded, google, exists, failed, system) |
| `GET /api/resources` | CPU, memory, disk I/O and Google response time |
| `GET /api/settings` | Settings (secrets are never returned) |
| `GET /api/thumb?path=Photos/x.jpg` | WebP thumbnail |
| `GET /api/insights?tz=330` | Upload times, average sizes, busiest hours and weekdays, file types |
| `GET /api/metrics?hours=24` | Per-minute speed, CPU, memory and Google response time (up to 14 days) |
| `GET /api/logs?level=all&q=&before=&limit=200` | App log (`level` = all, info or error; error includes warnings) |
| `GET /api/about` | Release, backend, build and package versions |
| `GET /api/processes?days=371&tz=330` | Each process's items per day, by hour and by weekday |
| `GET /api/upload` | Whether the Upload page can save files, and free space |
| `POST /api/upload?name=IMG_1.jpg&mtime=<ms>` | Upload one photo or video (raw file as the body) |

Each day has `photos`, `videos`, `photo_bytes`, `video_bytes`, `existing`, `failed`,
`net_sent`, `net_received`, `photo_ms`, `video_ms` (time spent uploading), `peak_up`, `peak_down`
(bytes per second).
</details>

## Development

```text
backend/        Python: sync engine and REST API
  sync.py         web server, scanning, uploads and Google work
  cli.py          the gpsync command
  provenance.py   where a file came from (Sources) and Snapchat date estimates
  snapchat.py     Snapchat Memories: the export list and finding memories in Google
  media.py        fingerprints and thumbnails
  notify.py       ntfy and Telegram alerts
  cache.py        Redis cache with a memory fallback
  tests/          pytest
  pyproject.toml, uv.lock
web/            TypeScript dashboard (Vite+, Tailwind CSS)
  src/views/      one file per page
  src/lib/        shared code (API, charts, icons, …)
scripts/        dependency updater, patch-release.Dockerfile and snapchat_export.py
docs/           README screenshots, PROMPTS.md (AI assistant prompts)
Dockerfile      builds web/, then the Python image
VERSION         release version (backend and dashboard have their own); CHANGELOG.md lists changes
```

The sync engine and REST API are Python, in `backend/`. The dashboard is TypeScript in `web/`,
styled with Tailwind CSS v4, with Lucide icons, hand-drawn SVG charts and the PhotoSwipe viewer, and
built with [Vite+](https://viteplus.dev) (`vp`: Vite, Rolldown, Oxlint, Oxfmt and Vitest in one tool).

In `web/`:

```sh
npm ci                     # exact versions from package-lock.json
npx vp dev                 # dev server; API calls go to the app on port 8527 (or API_URL)
npx vp check               # format, lint and type check
npx vp test                # tests
npx vp build               # build into web/dist, which sync.py serves
```

The Docker image builds the dashboard in a Node stage and copies only the result into the Python image.

The Python side uses [uv](https://docs.astral.sh/uv/) and [ruff](https://docs.astral.sh/ruff/). In `backend/`:

```sh
uv sync                    # install exact versions from uv.lock, plus ruff and pytest
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pytest              # tests (build the dashboard first), including checks that gpmc still has what sync.py uses
```

<details>
<summary><b>Design rules for the dashboard</b></summary>

Colours and components are in `web/src/style.css`.

- Surfaces, borders and text come from one neutral ramp per theme; depth is a surface step and a
  hairline, never a glow, a gradient or a coloured shadow.
- One accent per theme (`--accent`, `--meter`: moonlight blue at night, amber by day), used rarely:
  the selection, progress bars and the Sync ring. Chart series colours (`--s1`…`--s5`) only mean a
  series; green, amber and red only mean a state.
- Buttons, inputs and tabs share a 10px corner, cards 16px; the main button is `--primary`.
- Labels in sentence case, weights 400 and 500 (600 only for bold words in a sentence), counters
  in mono (`.meta`).
- Lucide icons in the text colour; spacing on a 4px grid.
- Motion is short and uses one curve (`--ease`). Nothing loops unless work is really running.
- Pages are built from the same parts (`web/src/lib/page.ts`): the header comes from the View's
  `subtitle` and `how` (main.ts draws it, with the Labs tag for routes in the Labs group), the
  summary numbers from `statRow()` and `setTile()`, the filter bar from `toolbar()` or the
  `.toolbar` classes. A new page uses these before adding its own.
- What each account has (uploaded, already in Google, new to sync, failed) is drawn by
  `accountBreakdown()` in `web/src/lib/account-breakdown.ts`, from the account rows of
  `/api/folders`; Sync and Folders both use it. The Overview's "Since your last visit" line is
  `web/src/lib/since-visit.ts` (kept in the browser's storage, no backend).
- The phone tabs show what's running: Sync gets a progress ring while files upload, and Overview
  turns into the background work running now with its progress.
</details>

<details>
<summary><b>Releases</b></summary>

- Three version numbers, each raised only when its part changes:
  - `VERSION`: the release (Docker tag, GitHub release, what Umbrel offers). It goes up with every
    release, because Umbrel only offers an update for a new tag. Add a `CHANGELOG.md` section too.
  - `backend/pyproject.toml` `version`: the server (`sync.py` and friends). Raised only when the
    backend changes.
  - `web/package.json` `version`: the dashboard. Raised only when the dashboard changes.

  A dashboard-only release raises `VERSION` and the dashboard version; the backend version stays.
  About shows all three (Release, Backend, Dashboard).
- `.github/workflows/release.yml` lints and tests every push (`vp check`, `vp test`, `vp build`,
  ruff, pytest). When `VERSION` has no git tag yet, it builds the image
  `kshanxs/gp-sync:<version>` on Docker Hub and `ghcr.io/kshanxs/gp-sync:<version>` (amd64 and
  arm64), tags the commit `v<version>` and makes a GitHub release with that version's
  `CHANGELOG.md` notes. The Umbrel store
  [kshanxs/kshanxs-umbrel-store](https://github.com/kshanxs/kshanxs-umbrel-store) checks for new
  images on a schedule and updates itself; Umbrel then shows **Update**.
- When only the app code and dashboard changed since the last release (the packages are the same),
  `scripts/patch-release.Dockerfile` builds the release on top of the previous image, so only a few
  MB are uploaded.
- Every Monday it tries the newest gpmc and packages (`scripts/update_deps.py`); if every test
  passes, it releases the next patch version on its own. If anything fails, nothing is released.
- All code and packages ship inside the image, pinned by digest in the store's
  `docker-compose.yml`, so a restart always runs the same tested build. Umbrel updates copy only
  `docker-compose.yml`, `umbrel-app.yml` and `hooks/`.
- The Umbrel app id stays `kshan-gphotos-sync`, so installed copies keep updating.
</details>

## License and credits

GP Sync is made by [kshanxs](https://github.com/kshanxs) and released under the
[MIT license](LICENSE). It builds on open-source work by others, credited on the app's About page:
[gpmc](https://github.com/xob0t/google_photos_mobile_client) by xob0t (the Google Photos client),
Turso, SQLite, Redis (optional), Pillow, pillow-heif, [PyAV](https://github.com/PyAV-Org/PyAV) (video
thumbnails, with FFmpeg's decoders), Python, uv, Ruff, Vite+, TypeScript, Tailwind CSS, Lucide and
[PhotoSwipe](https://photoswipe.com) (the photo viewer), plus ReVanced and GmsCore for getting the sign-in.
