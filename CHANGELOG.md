# Changelog

## 1.5.14

- Snapchat: each job has its own, named button again, in one card: "Add to album" (already in
  Google with the right date: nothing is uploaded), "Replace" (Google's later-dated copies give way
  to the memory) and "Upload" (not in Google). Start them in any order; each confirms what it will
  do. The single Start of 1.5.13 and the old Start all are gone.

## 1.5.13

- Snapchat: one Start button instead of four. A card above the lists says what Start will do, in
  numbers (added to the album, replaced, uploaded, extra copies deleted from Google, left out), and
  shows the progress once it runs. The group cards only sum up and pick the list below.

## 1.5.12

- Snapchat: comparing with Google's thumbnails no longer makes Google answer "too many requests"
  (HTTP 429). Thumbnails come in paced batches; when Google asks to slow down, what came is kept,
  the next batch waits (10 minutes, doubling up to 2 hours) and asks for fewer. The page says when
  it goes on instead of showing an error.
- Snapchat: far fewer thumbnails to compare. A shape only a handful of memories have (one 4:3
  memory, say) no longer pulls in every camera photo of that shape (on a 41,000-item library:
  18,690 thumbnails down to 11,037), and saves without a camera, the likely copies, come first.
- Snapchat: the missing-memories note names the missing part of the export ("1,146 from Apr 2024
  to Feb 2025") and counts the scattered rest apart, instead of one long range.

## 1.5.11

- Snapchat Memories, with their real dates. `scripts/snapchat_export.py` turns Snapchat's "Download
  My Data" zips into a folder (for example Photos/Snapchat): every photo and video gets Snapchat's
  exact date, time and place written inside, the text and stickers layer burned in, and a name with
  its local time. Run it again with more zips and only new memories are added; it says which
  months are missing from the zips.
- A new Snapchat page looks for each memory in Google Photos before anything is uploaded: the same
  file, a camera-roll copy taken in the same second, or one that looks the same (its fingerprint
  against Google's thumbnail; videos by their first frame), and every further copy of it. No
  memory stays in Google twice: one copy stays (the original, or one with the right date) and goes
  into the Snapchat album; when Google only has later-dated copies, the memory is uploaded with its
  date, time and place in their place (albums you made yourself don't carry over) (favourite, archive and your caption come back). Every copy that goes is kept
  on this server first (Downloads) and then deleted from Google; photos from a camera are never
  deleted. Pick another action for any memory, pick the account, pause, or look again. Nothing
  changes in Google until Start.
- Phones: pull to refresh comes out below the page's control strip, not over its Pause and Refresh
  buttons.
- Nothing in a Snapchat export folder is backed up until it is in the folder's list: the script
  marks the folder while it writes, and files other apps leave there are held back.
- Memory files are never backed up as they are, and camera-roll copies on this server that a memory
  takes the place of aren't uploaded as well. A copy that is uploaded (or fixed in Fix dates) gets
  the memory's date.

## 1.5.10

- Free storage, Working now: each item stays listed from download to Google's check ("Checking
  with Google") and leaves once it is freed, so one file can be followed step by step, as in 1.5.6.
  New items still start without waiting for the check, so freeing is as fast as before.
- Numbers that agree: Overview's strip and Running now count the same work; Library says how many
  files are still to back up, matching the count in the menu; Sync's ring is the share of files,
  with the share of space beside it; Copy accounts says what the other account had at the last read
  and how many GP Sync copied since; CPU is the share of the whole machine everywhere (0–100%).
- Nothing cut off: process cards on Overview, Insights tiles on phones, the speed limit choices,
  filters on Activity (they wrap instead), device names under Where your photos are, account chips
  and tiles on Free storage, and dates and folders in the photo grids.
- Rows that scroll sideways (Settings tabs on phones, heat maps) fade at the edge, and the heat maps
  open on the newest weeks.
- Sync: Today, Streak, Space and Last scan sit under Backup progress, beside Now, so no card is half
  empty. Insights no longer shows the upload hours and weekdays twice.
- Chart colours mean the same in both themes: blue for photos and this week, orange for videos and
  last week.
- Pause shows only when there is something to pause (Free storage, Copy accounts, Fix dates). Upload
  hours say when they are off. Sources asks about app saves only once some are found. The header's
  Add files button is hidden on the Upload page.
- Fix dates picks the account with the same chips as Free storage.
- Copy accounts shows a placeholder when a thumbnail can't load. Overview's Recent activity leaves
  out housekeeping such as "Settings saved". History shows the total uploaded instead of "Already in
  Google 0", and Activity's session total says "None yet" instead of "0 · 0 B".

## 1.5.9

- Pause and Refresh moved from the header into each page: a control strip under the title says
  what the page's job is doing, with one Pause/Resume and one Refresh. Overview pauses or resumes
  everything and scans; Sync pauses uploads and scans; Free storage, Copy accounts and Fix dates
  pause their own job and read the library from Google.
- No more repeated Resume buttons: the paused banners and the Pause buttons inside cards are gone.
- Each job has its own pause again. Overview's Pause everything switches every job's pause on, and
  Resume switches them all off, so nothing is held back by a different button. What 1.5.8 had
  paused stays paused after the update.
- Settings › Sync: the switch is "Pause uploads" again.

## 1.5.8

- One Pause for everything: the header's pause button now stops uploads, Free storage, copies, Keep
  in sync and date fixes (items under way finish their step). Before, it paused only uploads, so
  Free storage kept working while everything looked paused. A banner says when everything is paused,
  with a Resume button. Each page still has its own Pause and Resume for its job alone.
- Overview shows only what is live: Running now and Recent activity appear when they have something,
  and a process card shows only while it runs, waits, is paused or did something today. The cards
  left share the row evenly. Upload speed, download speed, CPU, memory, Network and At a glance stay.
- The upload history moved to the Sync page: Today, Streak, Where your photos are, Upload times and
  sizes, and Uploads per day.
- Free space on this machine, read from the disk the photos are on (any host): a Space card on Sync
  and a row in At a glance, amber when it runs low and red when almost full. A separate data disk
  gets its own row.
- Progress bars glide between updates instead of jumping and stopping, without the endless shine.
  Process cards show progress through the whole queue, so the bar only moves forward.
- Phones: the tab you tap gets a round highlight that fits the icon (no pill, no ripple).
- Copy accounts: the cards say "family@gmail.com has everything" or "Nothing copied yet" instead of
  "0 B", and "What to copy" says what to do when nothing is chosen.
- Works the same on any Docker host: Umbrel's name and hints show only with PLATFORM=umbrel, which
  the Umbrel app sets (HOST_NAME=Umbrel counts too). Elsewhere it says "this server" or HOST_NAME.
- Running now: the arrows point the right way.

## 1.5.7

- Faster Free storage and Copy accounts:
  - Checking with Google that new uploads don't count against storage now runs on its own, per
    account. One library read about once a minute covers every item waiting, with a longer wait
    each time Google doesn't answer. Downloads, uploads and new items no longer wait for it (before,
    new items waited for a full library read every minute, which took a while on big libraries).
  - Before downloading from Google, GP Sync looks for a file on this server with exactly the same
    checksum and copies it instead ("Copying from Umbrel"). The checksum is checked again while
    copying, and the original is never moved or changed.
- Keep in sync (Copy accounts): keep one account copied into another, or both ways. GP Sync
  compares both libraries straight away, then every hour, and copies only what's missing. Items
  already in both accounts are skipped by checksum, and copies are never sent back.
- A new Sync page holds normal backup: what uploads now and next, backup progress and each folder.
- Overview: Running now at the top, then a card per process (uploads, freeing storage, copies, date
  fixes) with what it is doing, what it did today and the last 14 days. Tap one for its page.
- History and Insights cover every process: each one's own history, streaks, best day and heat
  map, or everything together, and this week against last.
- Phones: the tabs are Overview, Sync, Add files, Activity and More. Sync gets a progress ring while
  files upload, and Overview turns into the work running now (freeing, copying, fixing dates) with
  its progress, taking turns when several run.
- No more flicker: pages change only what changed (numbers, bars, badges, new rows), so lists don't
  jump, thumbnails don't reload and charts keep their hover and tooltip.
- Free storage: each account shows "Nothing to free", "1.9 GB to free", "Freeing · 3 at once · 7
  left" or "Paused · 7 waiting", plus what was freed so far.
- Free storage, Fix dates and Copy accounts: the header's refresh button reads the library from
  Google (the pages' own refresh buttons are gone). "Read my library" shows only when it hasn't been
  read yet. The Moving now card shows the real count ("3 of 4") and how many wait in the queue.
- "Items at once" and "Files at once" go up to 4. A saved higher number becomes 4 automatically.
- The pause button: while work runs, two small moons orbit it and it glows softly.
- Card labels wrap onto two lines instead of being cut off.
- Time zone in Settings › Appearance: the system default (TZ, or the dashboard device's zone, for
  example India Standard Time) or any zone from the list. Days, upload hours, the weekly summary
  and the times shown follow it, daylight saving included.
- New README screenshots: Overview, and Overview, Sync and Upload on a phone.
- A release asks the Umbrel store to pick up the new version straight away.

## 1.5.6

- Free storage: the "paused" banner fits phones. The text runs the full width under its title,
  and its count is the real number of items waiting, not the 500 the list shows at most.

## 1.5.5

- Free storage, Time taken: the card shows the time Free storage actually ran (items moving at
  once counted once) instead of every item's time added up, which read hours too high. A new
  Time taken page shows running time, time per item and its median, speed, items per hour,
  where an item's time goes (download, replace and upload, Google's check), the slowest and
  fastest items, and running time per day.
- Freed per day shows at least the last 14 days with dates, so one busy day is one bar, not a
  block filling the chart.
- Left to free: the time left comes first and stays short ("About 3 h 30 min left · 7 items"),
  so long estimates are no longer cut off; the speed is in its tooltip.
- The header's pause button glows with a slow light ring while work runs (uploads, freeing,
  copies, date fixes) and stays still when paused. The header badge only shows scanning and
  uploading; paused and idle are shown by the button.
- The chosen option in every set of choices (Devices or Folders, Live/1 h/24 h, filters,
  photos or videos…) is highlighted with the accent colour, so it is clear which one is on.
- Copy accounts: two matching From and To cards with an account menu and a short summary each,
  and a labelled Swap button between them. Paused cards and banners point to the header's
  play button instead of showing a second Resume button (Copy accounts, Free storage, Fix dates).
- Phones: one account picker on Free storage (the account list), the account and its action on
  one row on Free storage and Fix dates, sets of five choices as an even 3 + 2, and Library's
  folder and sort menus side by side.

## 1.5.4

- Uploads go several at a time: 3 files at once per account by default (Settings › Sync, Files at
  once, up to 8). The speed limit is shared between them, and Overview lists every file uploading.
- Pause works at once: pausing Free storage or Copy accounts stops downloads in progress (they
  wait in the queue, nothing changes in Google). An item already deleted in Google still finishes
  its upload, so nothing is lost. The Working now card says "Pausing" until then.
- Working now shows a moving progress bar and the percentage for every item; replacing in Google
  shows a sliding bar. Progress bars on the thumbnails show up again.
- Free storage: "Left to free" replaces the GP Sync uploads card: what is still queued and about
  how long it takes at the speed of the last hour.
- Copy accounts copies several items at once (Free storage › Options, Items at once), shows each
  one, the average speed, time per item and time left, and the header's pause button pauses
  copying there. Starting and stopping ask in the app's own dialog.
- Fix dates can be paused per account, from the header's pause button on that page.
- Diagnostics list what Free storage and copies are working on.

## 1.5.3

- Fixed: Free storage said "Didn't work: the download is not exactly Google's original" for some
  videos. Google serves a copy it changed slightly (same size, an edited date inside) and lists
  that copy's checksum next to the original's. A download is now accepted when it matches a
  checksum Google gives for that item, and is uploaded again with its own checksum. Items that
  failed this way are tried again once.
- Fixed: items freed or copied between accounts got "{}" as their caption in Google Photos (the
  library list shows "no caption" that way). It is never copied again, and the "{}" is cleared
  once from items already moved.
- Free storage moves several items at once (4 by default, up to 16, per account), each still
  downloaded, checked, deleted in Google, uploaded again and confirmed, step by step.
- Free storage shows every account's progress at the top and opens on the account that is
  working, so coming back to the page no longer shows an empty main account. Items under way are
  listed first, with a Working now list. Fix dates and Copy accounts open the same way.
- The header's pause button pauses freeing on the Free storage page (uploads everywhere else).
- Free storage options: ask before freeing (or not, also from the dialog), keep the downloaded
  copies, items at once, and an optional "Freed by GP Sync" album.
- Freed page (click "Freed by GP Sync"): freed total, photos and videos, time taken, freed per
  day, and every freed item with a link to check it in Google Photos; kept copies play here.
- Overview: the donut slices, their legends and the stat tiles open the matching page (a folder,
  photos or videos only, Network, Activity, History, Health).
- Free storage lists only photos or only videos from the server, so those tabs fill up, as a
  thumbnail grid like Library, with "How it works" at the bottom.
- Items already gone from Google are skipped instead of failing; the data folder is created when
  missing; docs/PROMPTS.md explains running without Docker.

## 1.5.2

Updated after all tests passed: lucide 1.67.0 -> 1.70.0, @tailwindcss/vite 4.3.4 -> 4.3.5, tailwindcss 4.3.4 -> 4.3.5, vite-plus 1.4.0 -> 1.5.0.

## 1.5.1

- Fixed: Free storage over-counted what uses your Google storage. A photo that is also in albums is
  stored once by Google but was listed once per album, so the total could read far higher than
  Google's own (for example 131 GB instead of 70 GB). Each item is now counted once, and "Free all"
  frees each one once.
- The login-line prompt is reworded so AI assistants that refuse account-token steps understand it
  is your own account on your own device, and docs/PROMPTS.md now has copy-paste commands to read
  the line yourself without an assistant.

## 1.5.0

- Fixed: the repeated "Could not update album … 404" error. One item missing from Google (deleted,
  or moved to the Trash or Locked folder) made Google refuse a whole batch, so albums like Snapchat
  were never made. Items are now checked against the library first (a replaced item is found under
  its new key), and a refused batch is split so everything else still goes in.
- Downloads: when freeing storage, choose "Delete the downloaded copy after the upload". Turned
  off, the copies are kept on the server, per account, to save or delete on the new Downloads page.
- Diagnostics: one JSON file with versions, checks, settings (no secrets), what each worker is
  doing and the recent log and activity. Health › Diagnostics (JSON), or `gpsync diagnostics`.
- `gpsync` command in the image: status, logs, activity, check-login, refresh, storage, free,
  downloads and diagnostics (`docker exec <container> gpsync …`).
- README: an Attention section (free storage depends on Google, what deleting in Google loses,
  Live Photos and more), AI assistant prompts for the login line and for installing on a laptop,
  server or Umbrel (docs/PROMPTS.md), and new screenshots.

## 1.4.2

- Settings, regrouped: Backup (Sync, Folders, Google accounts), Google Photos (Albums, Dates,
  Sources, Duplicates, Google tools) and App. Every option lives there, including the per-app
  album, caption and archive choices and pausing account copies.
- Dates explains why some photos have no date and what the "Date unknown" album is for.
- Google accounts: each account with its folders and state, buttons for its storage and dates,
  Manage to check logins, read libraries or check dates for several accounts at once, and a prompt
  to give Claude Code or Codex to fetch the login line from your phone.
- Fix dates and Free storage pick accounts from a menu; links open them on a given account.
- Free storage frees photos only, videos only, or both, including everything at once ("Free all").
  Items shared with you, in the Trash or the Locked folder are never touched.
- Activity names what happened: Date fixed, Re-uploaded free, Copied, Deleted in Google,
  Downloaded, with a "Google changes" filter. Copies between accounts are now logged too.
- The light/dark switch is a rounded rectangle that fits its square knob. Appearance is simpler.

## 1.4.1

- The light/dark switch changes the page at the knob's own speed: the new theme spreads from the
  knob as it slides, follows your finger when you drag, and shrinks back if you drag back.
- Dark mode is neutral graphite instead of violet, with a moon-blue accent that matches the switch.
- Your time zone is saved whenever the dashboard opens. It was only saved with the Schedule or
  Alerts settings, so dates from file names, schedule hours and daily stats could be off by hours.
- Free storage checks the downloaded copy again, byte for byte, right before deleting Google's copy,
  and downloads it again if it is gone or damaged. Broken downloads no longer leave files behind.
- Warnings from the Google Photos library are one line each in the log (and count as problems),
  instead of a dozen wrapped fragments.

## 1.4.0

- Fix dates is automatic: GP Sync checks the file is unchanged, deletes Google's copy (Trash, then
  permanently) and uploads it again with the right date. No albums or manual steps.
- Free storage is automatic: items that count against your storage are downloaded, deleted in Google
  and uploaded again free, keeping the date Google showed (Snapchat saves included).
- Accounts work at the same time: Free storage, Fix dates and copies run in parallel per account and
  per copy pair. Overview lists everything running under "Also running".
- Every re-upload sends its original date to Google, and each job keeps its own date when several
  run at once.
- Backups reuse each account's Google login instead of signing in every round; parallel downloads
  reserve disk space; library reads are one at a time per account.
- Faster tests (32 s to 3 s) and Docker builds; releases can update the Umbrel store right away.

## 1.3.0

- New look: warm paper and amber by day, ink-black and indigo by night, with clean solid cards in
  place of frosted glass. The accent follows the theme, like the light of the new switch.
- Light/dark switch in the header and in Settings: a glass knob lit along its edge, blue below at
  night and amber above by day, whose pixel moon rebuilds into a sun. Tap or drag it.
- The header shows only the page title; each page's one-line description sits above its content.
- Shorter, plainer text across Fix dates, Sources, Free storage, Copy, Settings, About and Upload.
- Phones: text that was cut off wraps to a second line, filters that didn't fit become a tidy grid
  instead of hiding choices, item buttons line up in one row, and dialog buttons stack.

## 1.2.3

- Sources: a new page shows where every photo and video came from (a camera, Snapchat, a screenshot,
  Instagram, TikTok, WhatsApp, Telegram, or "saved from an app"), with the evidence for each.
- Items saved from apps can go into an album per app, get a searchable caption, or be archived.
  Nothing changes in Google until you choose, and your own captions are never replaced.
- Snapchat saves get an estimated save date (shown with its margin), worked out from your own camera
  photos. New uploads use it; items already in Google change only when you accept it in Fix dates.
- Dates in file names (WhatsApp, Telegram, screenshots, Pixel) are used for files with no date inside.
- Fixes: the "Date unknown" album adds new files reliably; local hours start on the hour in zones such
  as India; searches treat % and _ literally; uploads deleted here are stored again; a reconnecting
  upload no longer races its retry; thumbnails are written safely in parallel; replacing a Google
  login removes the old saved copy; malformed requests get a clear error.

## 1.2.2

- Fix dates: recheck selected or all waiting items, with clearer status views and animated transitions.
- Show known before-fix dates and distinguish Google-verified dates from upload estimates; keep Google
  Photos links available when known. Older fixes may not have a recorded before-fix date.
- Overview: make device and folder switches responsive and preserve the selected view.
- Folders and devices: distinguish adding files from changing backup priority, and move priority
  controls below the folder statistics.

## 1.2.1

- Copy accounts: choose individual photos and videos or copy all items matching your filters.
  Duplicates are skipped; copying albums is not available with the connected Google login.
- Folders and devices: track daily photo and video arrivals and see which are waiting, backed up
  or failed. Arrival dates are estimates based on filesystem timestamps.
- Refreshed the dashboard with layered glass surfaces, dimensional details, updated navigation
  and clearer overview metrics in both light and dark themes.
- Made the weekly-summary scheduling test independent of the current date.

## 1.2.0

- Uploads per day and the streak refresh as soon as new files upload (they waited up to
  two minutes on Overview); the History page now refreshes too (it only loaded once)
- The streak counts the same uploads as the heat map, and says "1 active day" (not "days")
- Right dates for photos and videos with no date inside (Snapchat saves, screenshots, chat
  pictures): Google Photos dates those by the upload, which used to be the time the file was copied
  to the server. GP Sync now sends the best date it knows: one you pick, else the earliest identical
  copy on the server
- Fix dates page: lists uploaded files without a date, the date Google Photos shows and the date
  they should have (an earlier date already in Google is kept). Set date picks your own; Fix waits
  for you to delete the item in Google Photos (GP Sync never deletes there), then uploads it again
  with the right date and brings back favourite, archive, caption and GP Sync's albums
- Album for files without a date ("Date unknown" by default, on): asked once after connecting a
  Google account (and once after this update); change it in Settings › Albums & duplicates.
  Library has a "No date" filter and badge
- Date facts are kept in the Turso database

## 1.1.5

- Upload first: pick folders or devices whose photos go to Google before the rest (Upload first
  on a folder or device; reorder or remove them in the Upload order card on Folders). A change
  re-orders the files still waiting right away
- Overview: "Where your photos are" card with pie charts per device (or folder) and photos vs
  videos, by files or size, with each device's backed-up share
- Folders: the chart sits in the summary card at the top (it was below the endless photo grid);
  a phone's folder (Photos/iPhone) shows as a device everywhere and opens the device view
- Copy accounts: every connected account is a card under "Copy from" and "Copy to"; pick any
  two (picking one that's on the other side swaps them); shows how many accounts are connected.
  With one account the empty card above "Connect a second Google account" is gone
- After an update, open dashboards reload by themselves, so nobody keeps the old page

## 1.1.4

- Folders and Devices only list and count the folders you back up; others (like Downloads) are
  named in a short note with a link to choose folders
- Every Folders page shows a photos and videos grid, also the first one (newest first, from all
  backed-up folders)
- Library's folder menu only offers backed-up folders (others looked empty there)

## 1.1.3

- Folders redesigned: devices and folders come first, under one compact summary (the chart moved
  to the bottom, with its 7 d to 1 y switch). Each device shows thumbnails of its newest photos
- Opening a device or folder shows its photos and videos right there, backed up or not, with
  In Google / Waiting / Failed badges, photo/video filter, sorting and endless scrolling. Before,
  folders opened Library, which only lists uploaded files, so many looked empty
- Backup apps' 00..ff folders are no longer listed: the device's photos are shown instead
- A device keeps its name in the path (Devices › iPhone), and files named by a long code show
  their date as the title

## 1.1.2

- New Copy accounts page: copies everything one connected Google account owns to another,
  newest first, one file at a time. Items the other account already has are skipped. Each file
  is downloaded, checked to be Google's original, uploaded the free way, and its favourite,
  archive and caption come along; then the copy on the server is removed. Filter by photos,
  videos and years; pause, resume, stop and retry failed ones. Nothing is deleted in either
  account. Every 25 copies GP Sync checks with Google that they don't count against the other
  account's storage, and pauses if they do. Albums, people names and shared albums don't come over
- Duplicates are never copied: items the other account already has, files Google reports as
  already there while uploading, and the same file twice in the account copied from. A
  "Duplicates found" card shows how many (and how much space), with a list of them
- Free storage: an item is asked about at most every 2 minutes while it waits for you, a new
  upload is checked once a minute (it gave up after a few seconds before), and a download that
  hits an internet or Google outage waits instead of failing
- One Google login per account is reused, instead of a new one for every file

## 1.1.0

- New Folders page (Insights): photos, videos, size and how much is backed up for any folder,
  opened one level at a time, plus a Devices view with one row per phone (the folders phone
  backup apps make, such as Photos/iPhone; "Pixel" and "Pixel (2)" count as one device)
- Added per day: a chart of new photos and videos for the folder or device (7 days to a year),
  and a small trend line with the new count for each subfolder or device
- Files are dated by when they arrived on the server, not the photo's own date
- Overview folder names open the Folders page; Library can show any folder, however deep
- New Free storage page: lists the photos and videos that count against your Google storage,
  biggest first, and moves them to free uploads one at a time. GP Sync downloads each one and
  checks it is exactly Google's original; you delete it in Google Photos (GP Sync never deletes
  anything there); GP Sync notices, uploads it again the free way with its date, favourite,
  archive and caption, and checks with Google that it no longer counts. Albums and shared links
  don't come back. If Google counts a re-upload, everything stops
- The page also shows whether GP Sync's own uploads count against storage (they shouldn't)

## 1.0.3

- Health: the page no longer fails to load when a Google login file is named differently from
  the account inside it, and Remove now deletes such a login too
- Health: if the page can't reach the app it says so instead of staying empty
- Settings › App & appearance: the Install button appears as soon as the browser offers to install
- Settings: an old or mistyped settings link opens Sync with Sync marked in the menu
- App log download is named gp-sync-log-<date>.txt
- Code: Python moved to backend/, the dashboard stays in web/ (files moved with git, history kept)

## 1.0.2

- Settings menu: each section has a colored icon (like iOS Settings), the rows have a little
  space between them, and hovering lights up only the row under the pointer (the soft glow no
  longer spills over the rows next to it); the open section is marked with a blue edge
- App & appearance, redone: pick the theme from three small pictures of the dashboard
  (System, Dark, Light) at the top; the change fades in across the page, and System shows
  whether the device is in dark or light mode right now
- Install as an app: a clearer card with the steps for this device in one place
- What works on this device: a tidy two-column list with a count ("4 of 7")
- Every settings section starts with its title and a one-line explanation
- Save and other buttons sit in one bar at the end of each section, with a short note; on
  phones they fill the width so they are easy to tap
- Sync: the status card no longer repeats the "Sync" title, and the Pause, Scan now and Retry
  buttons line up on phones
- Phones: the section tabs show their icons, and the open one stays in view

## 1.0.1

- Overview: Customize is now a small "Edit" link (next to the page subtitle on wider screens,
  above the cards on phones) instead of a big button on top of the page
- Overview: the backup ring's glow no longer shows a cut-off square edge (light mode), and an
  empty ring no longer shows a dot
- Upload on Android: "Take photo" and "Record video" are separate buttons, so the camera opens
  (one button asking for both opened nothing on Android); iPhone keeps one button
- Upload: "Where files go" works smoothly on phones: choosing or adding a folder updates the card
  in place, the Add button no longer gets squeezed, Enter adds the folder
- Phones: new pull to refresh: a small chip with a drawing circle follows your finger; the
  header and page no longer get dragged down
- Phone tab bar: the Upload button is a clean circle with a soft shadow (no square halo)
- Light mode: softer glows on blue buttons and text boxes; no outline left on buttons after a
  tap (keyboard users still get a clear focus ring)
- History: the upload time card no longer mentions an old version number
- README: more screenshots (phone and desktop)

## 1.0.0

First release of GP Sync:

- Backs up the photos and videos in Umbrel's Photos and Videos folders to Google Photos in
  original quality, every 5 minutes; nothing on Umbrel is changed or deleted
- Up to 5 Google accounts: back each folder up to one or several of them at once (the first
  is the main backup, the others get copies, tracked and retried per account)
- Folder choice, albums, duplicate finder, alerts (ntfy, Telegram)
- Dashboard inspired by iOS and Material 3: frosted-glass cards, pill buttons, tonal badges, system
  fonts, tuned dark and light themes, and charts with gentle depth (color-blind-safe series)
- Live status, network and resource charts, history, insights, photo viewer
- Upload from your phone (resumable): choose files or use the camera, screen kept on while
  uploading; installable as a phone app (iPhone, Android, desktop), with a list of what works on each device;
  44px touch targets, no zoom on text fields, clear messages
- Runs anywhere Docker runs (Linux, Windows, macOS, NAS, Raspberry Pi, Umbrel) and backs up any
  mounted folder, including an Immich library; optional sign-in page (PASSWORD), folder exclusions
- Light when idle: cache in the app's memory, answers the browser keeps, versioned thumbnails
