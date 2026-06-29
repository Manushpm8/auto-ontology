# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Manual test for the server-side ("api") analytics capture path.

Simulates a non-browser caller (like the NAT ``gsf_query`` plugin) hitting the
GSF frontend proxy ``POST /api/chat/completions`` WITHOUT the ``x-gsf-source:
app`` header, so the proxy route records analytics itself (``source='api'``).

Flow:
  1. Sign in with the bootstrap admin via Better Auth (cookie session) — the
     proxy route is cookie-session gated, so an authenticated session is
     required to reach it.
  2. Stream the chat completion, printing step + result events.
  3. Read back ``GET /api/analytics`` (admin only) and show the freshly
     captured row so you can confirm ``source='api'`` and the response backfill.

Run (from repo root, with the frontend on :3000 and backend on :3001 up):

    uv run python scripts/test_api_chat.py --question "How many rows are in <table>?"

Useful flags: --url, --email, --password, --source, --no-verify.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_env_defaults() -> dict[str, str]:
    """Best-effort parse of the repo-root .env for sensible CLI defaults."""
    values: dict[str, str] = {}
    if not _ENV_PATH.exists():
        return values
    for raw in _ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def _parse_args() -> argparse.Namespace:
    env = _load_env_defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=env.get("APP_URL", "http://localhost:3000"),
        help="Base URL of the GSF frontend (the proxy that records analytics).",
    )
    parser.add_argument(
        "--email",
        default=env.get("GSF_ADMIN_EMAIL", "admin@nvidia.com"),
        help="Admin email for Better Auth sign-in.",
    )
    parser.add_argument(
        "--password",
        default=env.get("GSF_ADMIN_PASSWORD", "qwerty123"),
        help="Admin password for Better Auth sign-in.",
    )
    parser.add_argument(
        "--question",
        default="Hello, what data can you query?",
        help="Question to send to the GSF assistant.",
    )
    parser.add_argument(
        "--source",
        default="api",
        help="Value for the x-gsf-source header. Use 'app' to test the "
        "skip-capture path; anything else (default 'api') triggers capture.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Overall request timeout in seconds.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the post-call GET /api/analytics verification step.",
    )
    return parser.parse_args()


def _sign_in(client: httpx.Client, base_url: str, email: str, password: str) -> None:
    """Authenticate via Better Auth; the session cookie lands in the client jar."""
    resp = client.post(
        f"{base_url}/api/auth/sign-in/email",
        json={"email": email, "password": password},
    )
    if resp.status_code != 200:
        print(
            f"[sign-in] FAILED ({resp.status_code}): {resp.text[:300]}\n"
            "Is the frontend running on this URL? (it seeds the admin on boot)",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(f"[sign-in] OK as {email}")


def _stream_chat(
    client: httpx.Client,
    base_url: str,
    question: str,
    source: str,
) -> tuple[str | None, str | None]:
    """Stream the SSE chat completion, printing events. Returns (answer, sql)."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "x-gsf-source": source,
    }
    answer: str | None = None
    sql: str | None = None

    print(f"[chat] POST /api/chat/completions (x-gsf-source: {source})")
    print(f"[chat] question: {question!r}\n")

    with client.stream(
        "POST",
        f"{base_url}/api/chat/completions",
        headers=headers,
        json={"question": question},
    ) as resp:
        if resp.status_code != 200:
            body = resp.read().decode(errors="replace")
            print(f"[chat] HTTP {resp.status_code}: {body[:500]}", file=sys.stderr)
            raise SystemExit(1)

        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "step":
                print(f"  · step: {event.get('label') or event.get('node')}")
            elif etype == "result":
                ans = event.get("answer") or {}
                answer = ans.get("response")
                sql = ans.get("sql_code")
            elif etype == "error":
                print(f"  ! error: {event.get('message')}", file=sys.stderr)

    print("\n[chat] final answer:")
    print(answer or "(none)")
    if sql:
        print("\n[chat] SQL:")
        print(sql)
    return answer, sql


def _verify_analytics(client: httpx.Client, base_url: str, question: str) -> None:
    """Fetch recent analytics rows and show the one matching this question."""
    resp = client.get(f"{base_url}/api/analytics", params={"limit": 5})
    if resp.status_code != 200:
        print(
            f"[verify] could not read analytics ({resp.status_code}): "
            f"{resp.text[:200]}",
            file=sys.stderr,
        )
        return

    rows = resp.json().get("data", [])
    match = next((r for r in rows if r.get("question") == question), None)
    if match is None:
        print(
            "[verify] no analytics row found for this question yet. The proxy "
            "backfills the response after the stream closes — try re-running "
            "verify, or check the most recent rows below:"
        )
        for row in rows[:3]:
            print(f"  - source={row.get('source')!r} q={row.get('question')!r:.60}")
        return

    print("\n[verify] analytics row captured:")
    print(f"  id:        {match.get('id')}")
    print(f"  source:    {match.get('source')!r}")
    print(f"  userName:  {match.get('userName')!r}")
    print(f"  question:  {match.get('question')!r}")
    print(f"  response:  {(match.get('response') or '(pending)')!r}")
    print(f"  sql:       {match.get('sql')!r}")
    print(f"  qMsgId:    {match.get('questionMessageId')!r}")


def main() -> None:
    args = _parse_args()
    base_url = args.url.rstrip("/")
    timeout = httpx.Timeout(args.timeout, connect=10.0)

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        _sign_in(client, base_url, args.email, args.password)
        _stream_chat(client, base_url, args.question, args.source)

        if args.no_verify:
            return
        # The proxy backfills the response in an `after()` hook once the stream
        # closes; give it a brief moment before reading analytics back.
        time.sleep(1.5)
        _verify_analytics(client, base_url, args.question)


if __name__ == "__main__":
    main()
