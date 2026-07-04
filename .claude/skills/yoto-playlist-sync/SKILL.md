---
name: yoto-playlist-sync
description: Operate, troubleshoot, and extend the yoto-podcast-feed project — auto-syncs a YouTube playlist to a Yoto "Make Your Own" card via the Yoto developer API. Use when the user asks about their Yoto card, the "Baby Playlist" sync, GitHub Action failures in yoto-podcast-feed, or wants to add another playlist/card.
---

# Yoto Playlist Sync

## What this project does

Repo: `~/workspace/yoto-podcast-feed` (GitHub: `d4rkb1ue/yoto-podcast-feed`, public).

Watches a YouTube playlist (the user's "favorites" collection for his son) and
automatically pushes new videos — as audio + a per-track icon — into a Yoto
"Make Your Own" (MYO) card, so the content shows up on the physical Yoto
player without any manual card-making. A GitHub Actions workflow runs this
every 6 hours.

Current card: title "Baby Playlist", `cardId` stored in `config.json`.
Playlist: `https://www.youtube.com/playlist?list=PLpl-_41eH5mJ2GNajs67iX-sY92UiHSQj`
(nursery rhymes / kids songs the user favorited on YouTube; content ownership
already discussed and accepted by the user — don't re-litigate that).

## Architecture

- `config.json` — `playlist_url`, `title`, `cardId` of the Yoto card being kept in sync.
- `manifest.json` — one entry per synced YouTube video: `id`, `title`, `pub_date`,
  `trackUrl` (`yoto:#<transcodedSha256>`), `icon16x16` (`yoto:#<mediaId>`),
  `duration`, `fileSize`, `channels`, `format`. This is the source of truth for
  "have we already synced this video" — dedup is by YouTube video id.
- `scripts/sync_to_yoto.py` — main entrypoint. Refreshes the Yoto access token,
  diffs the playlist against `manifest.json`, downloads+uploads new episodes,
  rebuilds the full `chapters` array, and calls `createOrUpdateContent`.
- `scripts/yoto_client.py` — thin Yoto API wrapper (token refresh, audio
  upload+transcode polling, icon upload, content create/update). All HTTP via
  `curl` subprocess calls (not `requests`/`urllib`) — see gotchas below for why.
- `scripts/oauth_login.py` — one-time interactive OAuth loopback flow
  (`http://127.0.0.1:8787/callback`) to mint the initial refresh token. Only
  needs to be re-run if the refresh token chain gets broken (see Troubleshooting).
- `.github/workflows/sync.yml` — cron (every 6h) + manual dispatch. Runs the
  sync, persists the rotated refresh token back into the repo secret, commits
  `manifest.json`/`config.json` if changed.
- `.claude/skills/yoto-playlist-sync/` (this file) — operational knowledge, so
  a fresh session doesn't have to rediscover the Yoto API by trial and error.

Repo secrets: `YOTO_CLIENT_ID`, `YOTO_REFRESH_TOKEN`, `SECRETS_ADMIN_TOKEN`
(the user's own `gh` CLI token, reused so the workflow can call `gh secret set`
to persist the rotated refresh token — see gotcha below).

## Why this exists (history, don't redo this exploration)

Two earlier approaches were tried and abandoned — if a future session is
tempted to "just use RSS," stop and read this:

1. **GitHub Pages + podcast RSS feed** (first attempt): generated `feed.xml`,
   hosted mp3s on GitHub Pages, added it as a Podcast in the Yoto app. Worked
   for playback, but every episode showed a generic "8 dots" icon. Root cause
   (confirmed via Yoto community feature request, still open as of this
   writing): **Yoto's RSS/podcast auto-import does not support per-episode
   custom icons at all**, regardless of `itunes:image` correctness. This is a
   product limitation, not a feed-formatting bug — don't waste time re-trying
   RSS `itunes:image` variations.
2. **Direct Yoto API** (current approach): bypasses the RSS importer entirely.
   Uploads audio + a real 16x16 icon straight into a normal MYO "Playlist"
   card via `createOrUpdateContent`. This is the only path that gets both
   auto-sync *and* custom per-episode icons.

## Yoto API gotchas (hard-won, mostly undocumented on yoto.dev)

- **Auth**: OAuth loopback/PKCE flow, not device_code (device_code is
  explicitly deprecated — docs say "do not use it for new apps"). Endpoints:
  authorize `https://login.yotoplay.com/authorize`, token
  `https://login.yotoplay.com/oauth/token`. Required scopes:
  `offline_access user:content:manage user:icons:manage family:library:view`.
- **Refresh tokens rotate on every use.** If the rotated token isn't persisted,
  the *next* automated run's refresh call can fail outright (Auth0-style reuse
  detection can revoke the whole token family). This is why the workflow has a
  "persist rotated refresh token" step calling `gh secret set` — don't remove it.
- **Python's stdlib SSL on this machine can't verify Yoto's/GitHub's TLS chain**
  (`CERTIFICATE_VERIFY_FAILED`, missing local issuer cert — a known macOS
  python.org-installer issue, "Install Certificates.command" not run). Workaround
  used throughout: shell out to `curl` instead of `urllib.request`. Keep doing
  this rather than fighting Python's cert store.
- **Audio upload is a 3-step dance, not 1:**
  1. `GET /media/transcode/audio/uploadUrl?sha256=<hex>&filename=<name>` →
     `{"upload": {"uploadId", "uploadUrl"}}`. `uploadUrl` is `null` if a file
     with that sha256 already exists (dedup) — that's fine, skip the PUT.
  2. If not null, `PUT` the raw file bytes to `uploadUrl` (presigned S3 URL).
  3. **Must then poll** `GET /media/upload/{uploadId}/transcoded?loudnorm=false`
     until the response contains `transcode.transcodedSha256`. The `trackUrl`
     for `createOrUpdateContent` is `yoto:#<transcodedSha256>` — **the
     transcoded hash, not the original upload sha256.** Using the original
     sha256 produces a confusing `"Media file not found... upload and
     transcode process"` error that looks like a timing issue but isn't.
     Also pull `duration`/`fileSize`/`channels`/`format` from
     `transcode.transcodedInfo`, not from local ffprobe — Yoto transcodes to
     its own codec (seen: opus/ogg) with different duration/size/channels.
- **Icon uploads reject webp.** yt-dlp's `--write-thumbnail` gives you `.webp`
  by default — convert to `.jpg` with ffmpeg first (`Unsupported image format.
  Please provide PNG, JPEG, or GIF.` otherwise). Use
  `POST /media/displayIcons/user/me/upload?autoConvert=true&filename=...`
  with the image bytes as the raw body — `autoConvert=true` makes Yoto's
  server resize to the required 16x16 for you. Response's
  `displayIcon.mediaId` is what goes into `icon16x16` as `yoto:#<mediaId>`.
- **`channels` field is an enum**, not a number: must be the literal string
  `"stereo"` or `"mono"` (sending `"2"` gives `Invalid option: expected one of
  "stereo"|"mono"`).
- **`createOrUpdateContent`** (`POST https://api.yotoplay.com/content`, scope
  `user:content:manage`): pass `cardId` to update an existing card, omit it to
  create a new one (response gives you the new `cardId`). Body replaces the
  *entire* `content.chapters` array each call — always send the full episode
  list from `manifest.json`, not a diff.
- Official reference implementation that resolved most of the above by
  reading real source instead of guessing: `github.com/yotoplay/examples`
  (`vanilla-js-html/src/upload.js`) and `github.com/bcomnes/yoto-nodejs-client`.
  If the API changes again, clone those first before re-guessing endpoints.

## Common tasks

**Check recent sync runs:**
```
cd ~/workspace/yoto-podcast-feed && gh run list --limit 5
```

**Manually trigger a sync now** (don't wait for the 6h cron):
```
cd ~/workspace/yoto-podcast-feed && gh workflow run sync.yml
```

**Run the sync locally** (needs `.yoto_tokens.json`, gitignored, from a prior
`oauth_login.py` run — see Troubleshooting if it's missing/stale):
```
cd ~/workspace/yoto-podcast-feed && python3 scripts/sync_to_yoto.py
```

**Add another playlist → another card**: this is currently single-playlist/
single-card by design (`config.json` has one `playlist_url`/`cardId`). To
support more, the cleanest extension is a list of `{playlist_url, title,
cardId}` entries in `config.json` and a loop in `main()` — not built yet,
build it if asked rather than hacking a second copy of the script.

## Troubleshooting

**Workflow failing on the refresh-token step / repeated auth errors**: the
refresh token chain is likely broken (e.g. it was used somewhere out of band,
or rotation reuse-detection fired). Fix: run `python3 scripts/oauth_login.py
<client_id>` locally (client_id is in `.yoto_tokens.json` if it still exists,
or re-check the yoto.dev dashboard app), which does a fresh browser login and
writes a new `.yoto_tokens.json`. Then push the new values into the repo
secrets:
```
cd ~/workspace/yoto-podcast-feed
python3 -c "import json; d=json.load(open('.yoto_tokens.json')); print(d['refresh_token'])" | gh secret set YOTO_REFRESH_TOKEN
```

**Workflow failing to persist the rotated refresh token (`gh secret set`
403/permission error)**: `SECRETS_ADMIN_TOKEN` (the user's `gh auth token`
value) has probably expired or been revoked. Re-set it:
```
gh secret set SECRETS_ADMIN_TOKEN --body "$(gh auth token)"
```

**A new episode uploaded but the icon still shows dots on the physical
player**: check the card via `GET /content/{cardId}` and confirm each
chapter's `display.icon16x16` is populated (see gotchas above for the exact
call chain if it's missing). Also remember: the physical card needs to have
been tapped/linked to the player at least once — content updates after that
don't require re-linking.

**User reports "I don't see new songs on the player"**: check
`gh run list` first (is the workflow even succeeding?), then check
`manifest.json` in the repo (did the video actually get picked up from the
playlist?), then check the live card content via the API before assuming
device-side sync lag.
