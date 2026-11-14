# Handoff

The shared memory between agent sessions on this project. A session reads this file first and
updates it last, so the next session (after a usage limit, a new chat or another tool) starts where
this one stopped. The repository holds what the code does; when the two disagree, the repository
wins, and the Work log row says so. Written by the handoff skill (`handoff.py log`) and by hand.

## Now

| Item | Owner | Status | Link |
|---|---|---|---|
| Release 1.8.0: on main, image not published yet | Claude | Blocked | main 8405e81 |

## Next up

1. Try two accounts on the Umbrel dashboard (pull main first).
2. After the fixes ship: on Umbrel, change a setting in the dashboard and confirm it saves.
3. Docker users who open GP Sync by a domain name without a password need ALLOWED_HOSTS: say so in the release notes.

## Waiting on the owner

Questions or a yes only the owner can give, with the options. Remove an item only when it is answered
in their own words, and record the answer in the Work log row.

- Push fix/cross-site-posts and open a PR? Release it as 1.7.5 and update the Umbrel store?
- Ship the parallel folder backup (now on origin/main as 6ecd4ef) in that release? The owner said to push it.

## Blocked

What cannot move, and why.

- GP Sync 1.8.0 is on main (8405e81) but not published: GitHub Actions won't start jobs ("account is locked due to a billing issue"), so CI can't build, tag or update the store. Building on umbrel works, but pushing the 165 MB Python layer to Docker Hub keeps dropping over umbrel's Wi-Fi (3 tries). No v1.8.0 tag, no GitHub release, store still on 1.7.5. 1.7.5 itself was also published by hand (no v1.7.5 tag). A push from homelab01 (owner-approved, image file built on umbrel at /home/umbrel/gp-sync-1.8.0.oci.tar, index sha256:7e19c2f0…) was refused: Docker Hub rejected umbrel's saved kshanxs token (it worked from umbrel earlier the same day) and then locked logins for a while. Needs a new Docker Hub access token, or GitHub billing fixed so CI uses its own secrets.

## Rules

- Work is plan-only unless the owner says build.
- No deletions, merges or sends (email, chat, posts, merges) without an explicit yes.
- Never invent numbers, prices, dates or facts; write Unknown.
- No secrets, tokens, passwords or personal data in this file.

## Page map

| Page | What it holds |
|---|---|
| `README.md` | What the project is and how to run it |
| `CHANGELOG.md` | Every release; `## Unreleased` holds what is not released yet |
| `backend/sync.py` | Sync engine and the HTTP server (routes in Handler.do_GET / post) |
| `web/src/` | The dashboard (Vite+, TypeScript) |

## Work log

Newest first. One row per session that changed anything; never edit another session's row.

| Date | Entry | Agent | Area | What changed | Links | Next step | Status |
|---|---|---|---|---|---|---|---|
| 2026-11-14T13:00+05:30 | Release 1.8.0 (blocked at publish) | Claude | Release | Owner said merge as 1.8.0 and update the store. Merged PR #3, pushed GP Sync 1.8.0 to main after running CI's checks locally (326 backend + 58 web tests, ruff, uv lock, vp check, build). Actions is locked for billing, so built the amd64+arm64 image on umbrel; 3 pushes to Docker Hub failed on the 165 MB layer (connection dropped over Wi-Fi). Store untouched. | main 8405e81 | Owner: fix GitHub billing, plug umbrel into Ethernet, or allow pushing from another machine; then tag v1.8.0, GitHub release, store update | Blocked |
| 2026-11-14T20:25+05:30 | Phone wording sweep | Claude | Dev | Checked every page and the backend's user-facing text for phone/device. Fixed where it didn't mean a phone: Upload page files are "Uploaded here" / "Received … from the Upload page" (backend log text), Upload folder in About and Health, Sync's link says Add files, README phone highlight no longer promises install, API table explains group=devices. Kept real phone references (Android login, phone alerts, iPhone formats, Google Photos app backups, camera models, this device's time zone). 326 backend + 58 web tests, ruff, vp check and the build pass on umbrel. Pushed to PR #3 | PR #3 | - | Waiting on owner |
| 2026-11-14T20:05+05:30 | Folders: no phones, just folders | Claude | Dev | Owner: on their Umbrel, ordinary folders were labelled as phones (the 00..ff guess). The Folders page now shows every folder as a folder (no phone icons, no merging of "X (2)", no "Also from this device"); Sync's chart says Subfolders instead of Devices. The backend still sets device and merges names for group=devices (unchanged). vp check, 58 tests and the build pass on umbrel. Pushed to PR #3 | PR #3 | Possibly rename the API's group=devices later | Waiting on owner |
| 2026-11-14T19:40+05:30 | Folders page as a browser | Claude | Dev | Owner chose: two-pane browser, photos first with tabs, priority pinned in the navigator. Rewrote web/src/views/folders.ts (one tree loaded a level at a time, phones merged as devices, numbers as filters, tabs Photos / Folders inside / Added); backup-priority.ts is now a compact switch with the same wording (its tests pass). Old #/folders links unchanged. vp check, 58 web tests and the build pass; checked in a headless browser on umbrel at desktop and phone width with test folders. Pushed to PR #3 | PR #3 | Owner: try it on the Umbrel with real phone folders | Waiting on owner |
| 2026-11-14T18:10+05:30 | Labs and one page frame | Claude | Dev | Owner asked: Labs group (Snapchat, Sources, Fix dates, chosen by the owner), the same page frame everywhere (lib/page.ts: header with Labs tag and How it works, statRow, toolbar), install buttons and Settings install card removed (manifest and share target kept, owner's choice). Library and Activity got summary rows. vp check, 58 web tests and the build pass on umbrel. Pushed to PR #3 | PR #3 | Owner: review PR #3; later candidates for Labs: Duplicates, Downloads | Waiting on owner |
| 2026-11-14T16:30+05:30 | Unslop the web UI | Claude | Dev | Ran the unslop pass on web/ (direction approved by the owner: graphite, photos carry the colour, Sync ring as the moment, mono counters; keep the moon/sun accent pair; flat mark in the sidebar only). Flat buttons and badges, sentence-case labels, weights 400/500, no glows, gradients, ripple or dot grid. Fixed a missing uploadSummary import that left Sync's Now card empty. vp check, 58 web tests and the build pass on umbrel | branch unslop-web-ui, PR | Owner: review the PR on a phone and desktop; README screenshots in docs/ still show 1.5.8 | Waiting on owner |
| 2026-11-12T03:20+05:30 | Sync page and copy progress | Cursor | Dev | Pushed 6ecd4ef to origin/main. Follow-up on main: Main library in stays a progress state, several accounts no longer share one jumping Now card, a failed copy stays in copies left, Snapchat holds and given-up copies are left out of an extra account's percent, and upload progress refreshes about twice a second without rereading the speed limit on every chunk. | main, after 6ecd4ef | Try two accounts on the Umbrel dashboard | Done |
| 2026-11-12T03:05+05:30 | Parallel folder backup per account | Claude | Dev | Folder backup now uploads to each account side by side (shared speed limit, per-account lanes in status). Make main in Settings → Folders; per-account cards on Sync; per-account percent, failed split, priority cap on Folders. 324 backend + 58 web tests pass on umbrel | local commit on main, not pushed | Owner: try with two accounts on Umbrel, then approve push and a release | Waiting on owner |
| 2026-11-12T02:36+05:30 | Fix all: DNS rebinding and test disk space | Claude | Dev | Without PASSWORD (not on Umbrel) only home-network Host names are answered; new ALLOWED_HOSTS setting. Copy test no longer needs 2 GB free. 320 backend tests pass | branch fix/cross-site-posts, not pushed | Owner: approve push, PR and release 1.7.5 | Waiting on owner |
| 2026-11-11T22:53+05:30 | Shipkit launch sweep: cross-site fix | Claude | Dev | Ran shipkit /break launch on umbrel: web 53 and backend 317 tests pass. Found and fixed: with no PASSWORD, other websites could add a Google account and redirect folders (403 now) | branch fix/cross-site-posts (90f2081), not pushed | Owner: approve push/PR and a release; then on Umbrel change a setting in the dashboard to confirm it still saves | Waiting on owner |
