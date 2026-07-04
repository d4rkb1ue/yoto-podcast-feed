#!/usr/bin/env python3
"""One-time interactive OAuth loopback login to obtain a Yoto refresh token."""
import base64
import hashlib
import http.server
import json
import secrets
import subprocess
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

CLIENT_ID = sys.argv[1] if len(sys.argv) > 1 else None
if not CLIENT_ID:
    print("Usage: oauth_login.py <client_id>", file=sys.stderr)
    sys.exit(1)

REDIRECT_URI = "http://127.0.0.1:8787/callback"
AUTHORIZE_URL = "https://login.yotoplay.com/authorize"
TOKEN_URL = "https://login.yotoplay.com/oauth/token"
SCOPE = "offline_access user:content:manage user:icons:manage family:library:view"
TOKENS_PATH = Path(__file__).resolve().parent.parent / ".yoto_tokens.json"

state = secrets.token_urlsafe(16)
code_verifier = secrets.token_urlsafe(64)
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).decode().rstrip("=")

result = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if params.get("state", [None])[0] != state:
            self.wfile.write(b"<h1>State mismatch, aborting.</h1>")
            result["error"] = "state mismatch"
        elif "code" in params:
            self.wfile.write(b"<h1>Login successful, you can close this tab.</h1>")
            result["code"] = params["code"][0]
        else:
            self.wfile.write(b"<h1>No code received.</h1>")
            result["error"] = params.get("error_description", ["unknown error"])[0]
        threading.Thread(target=self.server.shutdown).start()


def main():
    query = urllib.parse.urlencode({
        "audience": "https://api.yotoplay.com",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTHORIZE_URL}?{query}"

    server = http.server.HTTPServer(("127.0.0.1", 8787), CallbackHandler)
    print(f"Opening browser for login:\n{auth_url}\n")
    webbrowser.open(auth_url)
    server.serve_forever()

    if "error" in result:
        print(f"Login failed: {result['error']}", file=sys.stderr)
        sys.exit(1)

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier,
        "code": result["code"],
        "redirect_uri": REDIRECT_URI,
    })
    curl_result = subprocess.run(
        ["curl", "-s", "-X", "POST", TOKEN_URL,
         "-H", "Content-Type: application/x-www-form-urlencoded",
         "-d", body],
        capture_output=True, text=True, check=True,
    )
    tokens = json.loads(curl_result.stdout)
    if "refresh_token" not in tokens:
        print(f"Token exchange failed: {curl_result.stdout}", file=sys.stderr)
        sys.exit(1)

    TOKENS_PATH.write_text(json.dumps({
        "client_id": CLIENT_ID,
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens["access_token"],
    }, indent=2))
    TOKENS_PATH.chmod(0o600)
    print(f"Saved tokens to {TOKENS_PATH}")


if __name__ == "__main__":
    main()
