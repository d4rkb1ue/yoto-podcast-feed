#!/usr/bin/env python3
"""Pull new videos from a YouTube playlist, extract audio, regenerate podcast RSS feed."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
MANIFEST_PATH = ROOT / "manifest.json"
AUDIO_DIR = ROOT / "audio"
THUMBS_DIR = ROOT / "thumbs"
FEED_PATH = ROOT / "feed.xml"
COVER_PATH = ROOT / "cover.jpg"


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def fetch_playlist_entries(playlist_url):
    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "-J", playlist_url],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    return data.get("entries", [])


def download_audio(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(AUDIO_DIR / f"{video_id}.%(ext)s")
    subprocess.run(
        [
            "yt-dlp",
            "-x", "--audio-format", "mp3", "--audio-quality", "5",
            "--no-playlist",
            "-o", out_template,
            url,
        ],
        check=True,
    )
    mp3_path = AUDIO_DIR / f"{video_id}.mp3"
    if not mp3_path.exists():
        raise RuntimeError(f"expected {mp3_path} after download, not found")
    return mp3_path


def probe_duration_seconds(mp3_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(mp3_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return int(round(float(result.stdout.strip())))


def make_square_thumbnail(video_id, out_path, size):
    if out_path.exists():
        return
    url = f"https://www.youtube.com/watch?v={video_id}"
    tmp_template = str(ROOT / f"thumb_source_{video_id}")
    subprocess.run(
        ["yt-dlp", "--no-playlist", "--skip-download", "--write-thumbnail",
         "-o", tmp_template, url],
        check=True,
    )
    sources = list(ROOT.glob(f"thumb_source_{video_id}.*"))
    if not sources:
        return
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(sources[0]),
         "-vf", f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
         str(out_path)],
        check=True, capture_output=True,
    )
    for f in sources:
        f.unlink()


def ensure_cover_image(video_id):
    make_square_thumbnail(video_id, COVER_PATH, 1400)


def ensure_episode_thumbnail(video_id):
    THUMBS_DIR.mkdir(exist_ok=True)
    make_square_thumbnail(video_id, THUMBS_DIR / f"{video_id}.jpg", 500)


def fetch_title(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    result = subprocess.run(
        ["yt-dlp", "--no-playlist", "-J", url],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout).get("title", video_id)


def build_feed(config, episodes):
    base_url = config["pages_base_url"].rstrip("/")
    title = escape(config["title"])
    description = escape(config.get("description", ""))
    cover_url = f"{base_url}/cover.jpg"
    items = []
    for ep in sorted(episodes, key=lambda e: e["pub_date"], reverse=True):
        pub_dt = datetime.fromisoformat(ep["pub_date"])
        pub_rfc822 = format_datetime(pub_dt)
        enclosure_url = f"{base_url}/audio/{ep['id']}.mp3"
        thumb_url = f"{base_url}/thumbs/{ep['id']}.jpg"
        ep_title = escape(ep["title"])
        items.append(f"""
    <item>
      <title>{ep_title}</title>
      <description>{ep_title}</description>
      <guid isPermaLink="false">{ep['id']}</guid>
      <pubDate>{pub_rfc822}</pubDate>
      <itunes:duration>{ep['duration']}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
      <itunes:image href="{escape(thumb_url)}" />
      <enclosure url="{escape(enclosure_url)}" type="audio/mpeg" length="{ep['size']}" />
    </item>""")

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{title}</title>
    <link>{escape(base_url)}</link>
    <description>{description}</description>
    <itunes:author>{title}</itunes:author>
    <itunes:summary>{description}</itunes:summary>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{escape(cover_url)}" />
    <itunes:category text="Kids &amp; Family" />
    <image>
      <url>{escape(cover_url)}</url>
      <title>{title}</title>
      <link>{escape(base_url)}</link>
    </image>
    <language>en-us</language>{''.join(items)}
  </channel>
</rss>
"""
    FEED_PATH.write_text(feed)


def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        print("Missing config.json", file=sys.stderr)
        sys.exit(1)

    manifest = load_json(MANIFEST_PATH, {"episodes": []})
    known_ids = {ep["id"] for ep in manifest["episodes"]}

    AUDIO_DIR.mkdir(exist_ok=True)

    entries = fetch_playlist_entries(config["playlist_url"])
    new_count = 0
    for entry in entries:
        vid = entry.get("id")
        if not vid or vid in known_ids:
            continue
        print(f"New video found: {vid} - {entry.get('title')}")
        try:
            mp3_path = download_audio(vid)
        except subprocess.CalledProcessError as e:
            print(f"  failed to download {vid}: {e}", file=sys.stderr)
            continue
        title = entry.get("title") or fetch_title(vid)
        ensure_cover_image(vid)
        ensure_episode_thumbnail(vid)
        manifest["episodes"].append({
            "id": vid,
            "title": title,
            "pub_date": datetime.now(timezone.utc).isoformat(),
            "size": mp3_path.stat().st_size,
            "duration": probe_duration_seconds(mp3_path),
        })
        known_ids.add(vid)
        new_count += 1

    # backfill duration and thumbnail for episodes downloaded before these existed
    for ep in manifest["episodes"]:
        if "duration" not in ep:
            mp3_path = AUDIO_DIR / f"{ep['id']}.mp3"
            ep["duration"] = probe_duration_seconds(mp3_path)
        ensure_episode_thumbnail(ep["id"])

    if manifest["episodes"]:
        ensure_cover_image(manifest["episodes"][0]["id"])

    save_json(MANIFEST_PATH, manifest)
    build_feed(config, manifest["episodes"])
    print(f"Done. {new_count} new episode(s) added. {len(manifest['episodes'])} total.")


if __name__ == "__main__":
    main()
