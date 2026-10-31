"""Exchange a FYERS auth code for a daily access token and save it to .env.

Usage (within a couple of minutes of copying the auth code from the browser)::

    uv run python scripts/fyers_token.py <auth_code>

Reads FYERS_CLIENT_ID / FYERS_SECRET_KEY from .env, calls
POST /api/v3/validate-authcode, and rewrites the FYERS_ACCESS_TOKEN line
in .env. The full token is never printed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"
TOKEN_URL = "https://api-t1.fyers.in/api/v3/validate-authcode"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def save_token(path: Path, token: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    updated = [
        f"FYERS_ACCESS_TOKEN={token}" if line.startswith("FYERS_ACCESS_TOKEN=") else line
        for line in lines
    ]
    if not any(line.startswith("FYERS_ACCESS_TOKEN=") for line in lines):
        updated.append(f"FYERS_ACCESS_TOKEN={token}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def token_expiry(token: str) -> str:
    try:
        payload_b64 = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "==="))
        exp = datetime.fromtimestamp(int(payload["exp"]), tz=UTC).astimezone()
        return exp.strftime("%Y-%m-%d %H:%M %Z")
    except (IndexError, ValueError, KeyError):
        return "unknown"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: uv run python scripts/fyers_token.py <auth_code>")
        return 2
    code = sys.argv[1].strip()
    env = load_env(ENV_PATH)
    client_id = env.get("FYERS_CLIENT_ID", "")
    secret = env.get("FYERS_SECRET_KEY", "")
    if not client_id or not secret:
        print("FYERS_CLIENT_ID / FYERS_SECRET_KEY missing in .env")
        return 1

    app_id_hash = hashlib.sha256(f"{client_id}:{secret}".encode()).hexdigest()
    response = httpx.post(
        TOKEN_URL,
        json={"grant_type": "authorization_code", "appIdHash": app_id_hash, "code": code},
        timeout=20,
    )
    try:
        data = response.json()
    except ValueError:
        print(f"non-JSON response HTTP={response.status_code}: {response.text[:200]}")
        return 1
    if data.get("s") != "ok" or not data.get("access_token"):
        print(f"exchange failed: s={data.get('s')} code={data.get('code')} msg={data.get('message')}")
        return 1

    token = str(data["access_token"])
    save_token(ENV_PATH, token)
    print(f"saved FYERS_ACCESS_TOKEN (len={len(token)}, expires ~{token_expiry(token)})")
    print("restart the backend so it picks up the new token, then run: uv run python -m ai_trader fyers test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
