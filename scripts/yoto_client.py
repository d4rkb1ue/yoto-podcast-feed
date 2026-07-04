"""Minimal Yoto API client: token refresh, media upload, content create/update."""
import json
import subprocess
import time
import urllib.parse

API_BASE = "https://api.yotoplay.com"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"


def _curl_json(args):
    result = subprocess.run(["curl", "-s"] + args, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def refresh_tokens(client_id, refresh_token):
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    })
    tok = _curl_json(["-X", "POST", TOKEN_URL,
                       "-H", "Content-Type: application/x-www-form-urlencoded",
                       "-d", body])
    if "access_token" not in tok:
        raise RuntimeError(f"token refresh failed: {tok}")
    return tok["access_token"], tok.get("refresh_token", refresh_token)


def _auth_header(access_token):
    return f"Authorization: Bearer {access_token}"


def get_audio_upload_url(access_token, sha256, filename):
    url = f"{API_BASE}/media/transcode/audio/uploadUrl?" + urllib.parse.urlencode(
        {"sha256": sha256, "filename": filename})
    resp = _curl_json([url, "-H", _auth_header(access_token)])
    return resp["upload"]


def put_file(upload_url, file_path, content_type):
    subprocess.run(
        ["curl", "-s", "-X", "PUT", "--data-binary", f"@{file_path}",
         "-H", f"Content-Type: {content_type}", upload_url],
        check=True, capture_output=True,
    )


def poll_transcode(access_token, upload_id, timeout_s=120, interval_s=3):
    url = f"{API_BASE}/media/upload/{upload_id}/transcoded?loudnorm=false"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = _curl_json([url, "-H", _auth_header(access_token), "-H", "Accept: application/json"])
        transcode = resp.get("transcode", {})
        if "transcodedSha256" in transcode:
            return transcode
        time.sleep(interval_s)
    raise TimeoutError(f"transcode of upload {upload_id} did not finish in {timeout_s}s")


def upload_audio(access_token, file_path, sha256, filename):
    upload = get_audio_upload_url(access_token, sha256, filename)
    if upload["uploadUrl"]:
        put_file(upload["uploadUrl"], file_path, "audio/mpeg")
    return poll_transcode(access_token, upload["uploadId"])


def upload_custom_icon(access_token, file_path, filename, content_type="image/jpeg"):
    url = f"{API_BASE}/media/displayIcons/user/me/upload?" + urllib.parse.urlencode(
        {"autoConvert": "true", "filename": filename})
    resp = _curl_json(["-X", "POST", url,
                        "-H", _auth_header(access_token),
                        "-H", f"Content-Type: {content_type}",
                        "--data-binary", f"@{file_path}"])
    return resp["displayIcon"]["mediaId"]


def create_or_update_content(access_token, body_dict):
    tmp_path = "/tmp/_yoto_content_body.json"
    with open(tmp_path, "w") as f:
        json.dump(body_dict, f)
    result = _curl_json(["-X", "POST", f"{API_BASE}/content",
                          "-H", _auth_header(access_token),
                          "-H", "Content-Type: application/json",
                          "--data-binary", f"@{tmp_path}"])
    if "error" in result:
        raise RuntimeError(f"createOrUpdateContent failed: {result['error']}")
    return result["card"]
