# yoto-podcast-feed

Auto-syncs a YouTube playlist to a Yoto "Make Your Own" card via the Yoto API — new videos in the playlist get their audio and a per-track icon uploaded directly to Yoto, and the card is updated in place.

- `config.json` — playlist URL, card title, `cardId` of the Yoto MYO card being kept in sync.
- `manifest.json` — known episodes and their Yoto media references (`trackUrl`, `icon16x16`, duration, etc).
- `scripts/sync_to_yoto.py` — fetches new playlist entries, downloads audio + thumbnail via yt-dlp, uploads both to Yoto, updates the card.
- `scripts/yoto_client.py` — small Yoto API wrapper (token refresh, media upload, transcode polling, content create/update).
- `scripts/oauth_login.py` — one-time interactive OAuth loopback login to obtain a refresh token.
- `.github/workflows/sync.yml` — runs the sync every 6 hours, persists the rotated refresh token, commits `manifest.json`/`config.json`.

## Setup

1. Register a Public Client app at https://dashboard.yoto.dev with redirect URI `http://127.0.0.1:8787/callback` and scopes `offline_access user:content:manage user:icons:manage family:library:view`.
2. Run `python scripts/oauth_login.py <client_id>` locally — this opens a browser login and saves `.yoto_tokens.json` (gitignored).
3. Repo secrets (already set for this repo): `YOTO_CLIENT_ID`, `YOTO_REFRESH_TOKEN`, and `SECRETS_ADMIN_TOKEN` (a token with permission to update repo secrets, used to persist the rotated refresh token each run — Yoto rotates refresh tokens on every use).
4. First run creates the card and writes `cardId` into `config.json`. In the Yoto app, link a blank MYO card to it once (tap the card to the player) — future content updates apply automatically without re-linking.

## Manual run

```
python scripts/sync_to_yoto.py
```

Uses `.yoto_tokens.json` locally, or `YOTO_CLIENT_ID`/`YOTO_REFRESH_TOKEN` env vars in CI.
