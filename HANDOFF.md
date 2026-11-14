# Handoff

The shared memory between agent sessions on this project. A session reads this file first and
updates it last, so the next session (after a usage limit, a new chat or another tool) starts where
this one stopped. The repository holds what the code does; when the two disagree, the repository
wins, and the Work log row says so. Written by the handoff skill (`handoff.py log`) and by hand.

## Now

| Item | Owner | Status | Link |
|---|---|---|---|
| Unslop the web UI: PR open, not merged | Owner | Review | branch unslop-web-ui |

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

- Nothing.

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
| 2026-11-14T16:30+05:30 | Unslop the web UI | Claude | Dev | Ran the unslop pass on web/ (direction approved by the owner: graphite, photos carry the colour, Sync ring as the moment, mono counters; keep the moon/sun accent pair; flat mark in the sidebar only). Flat buttons and badges, sentence-case labels, weights 400/500, no glows, gradients, ripple or dot grid. Fixed a missing uploadSummary import that left Sync's Now card empty. vp check, 58 web tests and the build pass on umbrel | branch unslop-web-ui, PR | Owner: review the PR on a phone and desktop; README screenshots in docs/ still show 1.5.8 | Waiting on owner |
| 2026-11-12T03:20+05:30 | Sync page and copy progress | Cursor | Dev | Pushed 6ecd4ef to origin/main. Follow-up on main: Main library in stays a progress state, several accounts no longer share one jumping Now card, a failed copy stays in copies left, Snapchat holds and given-up copies are left out of an extra account's percent, and upload progress refreshes about twice a second without rereading the speed limit on every chunk. | main, after 6ecd4ef | Try two accounts on the Umbrel dashboard | Done |
| 2026-11-12T03:05+05:30 | Parallel folder backup per account | Claude | Dev | Folder backup now uploads to each account side by side (shared speed limit, per-account lanes in status). Make main in Settings → Folders; per-account cards on Sync; per-account percent, failed split, priority cap on Folders. 324 backend + 58 web tests pass on umbrel | local commit on main, not pushed | Owner: try with two accounts on Umbrel, then approve push and a release | Waiting on owner |
| 2026-11-12T02:36+05:30 | Fix all: DNS rebinding and test disk space | Claude | Dev | Without PASSWORD (not on Umbrel) only home-network Host names are answered; new ALLOWED_HOSTS setting. Copy test no longer needs 2 GB free. 320 backend tests pass | branch fix/cross-site-posts, not pushed | Owner: approve push, PR and release 1.7.5 | Waiting on owner |
| 2026-11-11T22:53+05:30 | Shipkit launch sweep: cross-site fix | Claude | Dev | Ran shipkit /break launch on umbrel: web 53 and backend 317 tests pass. Found and fixed: with no PASSWORD, other websites could add a Google account and redirect folders (403 now) | branch fix/cross-site-posts (90f2081), not pushed | Owner: approve push/PR and a release; then on Umbrel change a setting in the dashboard to confirm it still saves | Waiting on owner |
