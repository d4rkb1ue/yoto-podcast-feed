#!/usr/bin/env python3
"""Pull new videos from a YouTube playlist and sync them into a Yoto MYO card via the Yoto API."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yoto_client

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
MANIFEST_PATH = ROOT / "manifest.json"
TOKENS_PATH = ROOT / ".yoto_tokens.json"
WORK_DIR = ROOT / ".work"


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def load_tokens():
    client_id = os.environ.get("YOTO_CLIENT_ID")
    refresh_token = os.environ.get("YOTO_REFRESH_TOKEN")
    if client_id and refresh_token:
        return client_id, refresh_token
    tokens = load_json(TOKENS_PATH, None)
    if tokens is None:
        print("No Yoto credentials found (env vars or .yoto_tokens.json)", file=sys.stderr)
        sys.exit(1)
    return tokens["client_id"], tokens["refresh_token"]


def save_refresh_token(client_id, refresh_token, access_token):
    if TOKENS_PATH.exists() or "YOTO_CLIENT_ID" not in os.environ:
        save_json(TOKENS_PATH, {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "access_token": access_token,
        })
        TOKENS_PATH.chmod(0o600)
    # In CI, the rotated refresh_token is also printed so the workflow can
    # persist it back into the repo secret.
    if "GITHUB_ACTIONS" in os.environ:
        print(f"::add-mask::{refresh_token}")
        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            with open(gh_output, "a") as f:
                f.write(f"refresh_token={refresh_token}\n")


def fetch_playlist_entries(playlist_url):
    result = subprocess.run(
        ["yt-dlp", "--flat-playlist", "-J", playlist_url],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout).get("entries", [])


def download_audio(video_id, dest_dir):
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(dest_dir / f"{video_id}.%(ext)s")
    subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
         "--no-playlist", "-o", out_template, url],
        check=True,
    )
    mp3_path = dest_dir / f"{video_id}.mp3"
    if not mp3_path.exists():
        raise RuntimeError(f"expected {mp3_path} after download, not found")
    return mp3_path


def download_thumbnail(video_id, dest_dir):
    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(dest_dir / f"{video_id}_thumb")
    subprocess.run(
        ["yt-dlp", "--no-playlist", "--skip-download", "--write-thumbnail",
         "-o", out_template, url],
        check=True,
    )
    jpg_path = dest_dir / f"{video_id}_thumb.jpg"
    if jpg_path.exists():
        return jpg_path
    matches = list(dest_dir.glob(f"{video_id}_thumb.*"))
    if not matches:
        raise RuntimeError(f"no thumbnail downloaded for {video_id}")
    source = matches[0]
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), str(jpg_path)],
        check=True, capture_output=True,
    )
    source.unlink()
    return jpg_path


def sync_new_episode(entry, access_token, dest_dir):
    vid = entry["id"]
    title = entry.get("title") or vid
    print(f"  downloading audio for {vid}...")
    mp3_path = download_audio(vid, dest_dir)
    sha256 = hashlib.sha256(mp3_path.read_bytes()).hexdigest()

    print(f"  uploading + transcoding audio for {vid}...")
    transcode = yoto_client.upload_audio(access_token, mp3_path, sha256, f"{vid}.mp3")
    info = transcode["transcodedInfo"]

    print(f"  downloading + uploading icon for {vid}...")
    thumb_path = download_thumbnail(vid, dest_dir)
    icon_media_id = yoto_client.upload_custom_icon(access_token, thumb_path, thumb_path.name)

    mp3_path.unlink()
    thumb_path.unlink()

    return {
        "id": vid,
        "title": title,
        "pub_date": datetime.now(timezone.utc).isoformat(),
        "trackUrl": f"yoto:#{transcode['transcodedSha256']}",
        "icon16x16": f"yoto:#{icon_media_id}",
        "duration": info["duration"],
        "fileSize": info["fileSize"],
        "channels": info["channels"],
        "format": info["format"],
    }


def build_chapters(episodes):
    chapters = []
    for i, ep in enumerate(sorted(episodes, key=lambda e: e["pub_date"]), start=1):
        key = f"{i:02d}"
        display = {"icon16x16": ep["icon16x16"]}
        chapters.append({
            "key": key,
            "title": ep["title"],
            "overlayLabel": str(i),
            "display": display,
            "tracks": [{
                "key": key,
                "title": ep["title"],
                "overlayLabel": str(i),
                "trackUrl": ep["trackUrl"],
                "duration": ep["duration"],
                "fileSize": ep["fileSize"],
                "channels": ep["channels"],
                "format": ep["format"],
                "type": "audio",
                "display": display,
            }],
        })
    return chapters


def main():
    config = load_json(CONFIG_PATH, None)
    if config is None:
        print("Missing config.json", file=sys.stderr)
        sys.exit(1)

    client_id, refresh_token = load_tokens()
    access_token, refresh_token = yoto_client.refresh_tokens(client_id, refresh_token)
    save_refresh_token(client_id, refresh_token, access_token)

    manifest = load_json(MANIFEST_PATH, {"episodes": []})
    known_ids = {ep["id"] for ep in manifest["episodes"]}

    WORK_DIR.mkdir(exist_ok=True)

    entries = fetch_playlist_entries(config["playlist_url"])
    new_count = 0
    failed_ids = []
    for entry in entries:
        vid = entry.get("id")
        if not vid or vid in known_ids:
            continue
        print(f"New video: {vid} - {entry.get('title')}")
        try:
            episode = sync_new_episode(entry, access_token, WORK_DIR)
        except Exception as e:
            print(f"  failed to sync {vid}: {e}", file=sys.stderr)
            failed_ids.append(vid)
            continue
        manifest["episodes"].append(episode)
        known_ids.add(vid)
        new_count += 1

    if new_count == 0:
        if failed_ids:
            raise RuntimeError(f"Failed to sync {len(failed_ids)} video(s): {', '.join(failed_ids)}")
        print("No new episodes, skipping card update.")
        return

    body = {
        "title": config["title"],
        "content": {
            "chapters": build_chapters(manifest["episodes"]),
            "config": {"resumeTimeout": 2592000},
            "playbackType": "linear",
        },
    }
    if config.get("cardId"):
        body["cardId"] = config["cardId"]

    card = yoto_client.create_or_update_content(access_token, body)
    # Only mark episodes synced after the card update succeeds, so a failed
    # update is retried rather than leaving songs recorded but unplayable.
    save_json(MANIFEST_PATH, manifest)
    if not config.get("cardId"):
        config["cardId"] = card["cardId"]
        save_json(CONFIG_PATH, config)
        print(f"Created card: {card['cardId']}")

    print(f"Done. {new_count} new episode(s) added. {len(manifest['episodes'])} total.")
    if failed_ids:
        raise RuntimeError(f"Failed to sync {len(failed_ids)} video(s): {', '.join(failed_ids)}")


if __name__ == "__main__":
    main()
