# yoto-podcast-feed

Auto-syncs a YouTube playlist to a podcast RSS feed, hosted on GitHub Pages, for use as a Yoto MYO "Podcast" card.

- `config.json` — playlist URL, feed title/description, Pages base URL.
- `scripts/sync.py` — fetches new playlist entries, downloads audio via yt-dlp, updates `manifest.json` and `feed.xml`.
- `.github/workflows/sync.yml` — runs the sync every 6 hours, commits new audio + feed.

## Setup

1. Enable GitHub Pages: Settings → Pages → Source: `Deploy from a branch`, Branch: `main` / `root`.
2. Feed URL once Pages is live: `https://d4rkb1ue.github.io/yoto-podcast-feed/feed.xml`
3. In the Yoto app: Make Your Own → add a Podcast/RSS card → paste the feed URL.

## Manual run

```
pip install yt-dlp
python scripts/sync.py
```
