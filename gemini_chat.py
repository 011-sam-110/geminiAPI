#!/usr/bin/env python3
"""
Gemini Web Chat
Sends messages to the Gemini web interface using cookies from cookies.txt.

Setup:
  pip install requests

Usage:
  python gemini_chat.py               # loads cookies.txt from same directory
  python gemini_chat.py my_cookies.txt  # or specify a path
"""

import http.cookiejar
import json
import re
import sys
from pathlib import Path

import requests

BASE_URL = "https://gemini.google.com"
STREAM_URL = f"{BASE_URL}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
HEADERS = {
    "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    "referer": "https://gemini.google.com/",
    "x-same-domain": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}


def load_cookies(cookie_file: Path) -> requests.Session:
    """Load a Netscape-format cookie file into a requests Session."""
    jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
    jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session()
    session.cookies = jar  # type: ignore[assignment]
    return session


def get_session_tokens(session: requests.Session) -> tuple[str | None, str]:
    """Fetch the Gemini homepage to extract the CSRF token and build label."""
    resp = session.get(
        BASE_URL + "/",
        timeout=15,
        headers={"user-agent": HEADERS["user-agent"]},
    )
    resp.raise_for_status()

    at_match = re.search(r'"SNlM0e":"([^"]+)"', resp.text)
    bl_match = re.search(r'"cfb2h":"([^"]+)"', resp.text)

    at = at_match.group(1) if at_match else None
    bl = bl_match.group(1) if bl_match else "boq_assistant-bard-web-server_20260427.06_p4"
    return at, bl


def send_message(
    session: requests.Session,
    message: str,
    at: str,
    bl: str,
    conv_id: str = "",
    resp_id: str = "",
    choice_id: str = "",
) -> str:
    """POST a message to the StreamGenerate endpoint and return raw response text."""
    inner = json.dumps([
        [message, 0, None, [], None, None, 0],
        ["en-GB"],
        [conv_id, resp_id, choice_id],
    ])
    f_req = json.dumps([None, inner])

    resp = session.post(
        STREAM_URL,
        params={"bl": bl, "hl": "en-GB", "_reqid": "1", "rt": "c"},
        data={"at": at, "f.req": f_req},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def parse_response(raw: str) -> tuple[str | None, str, str, str]:
    """
    Parse the chunked streaming response and return:
      (text, conv_id, resp_id, choice_id)
    The endpoint streams partial text; we keep updating until the last chunk.
    """
    pattern = r'\["wrb\.fr",null,"((?:[^"\\]|\\.)*)"\]'

    last_text = None
    conv_id = resp_id = choice_id = ""

    for match in re.finditer(pattern, raw):
        raw_inner = match.group(1)
        try:
            inner_str = raw_inner.encode("utf-8").decode("unicode_escape")
            inner = json.loads(inner_str)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

        # Conversation IDs at inner[1]
        try:
            ids = inner[1]
            if isinstance(ids, list) and len(ids) >= 2:
                conv_id = ids[0] or conv_id
                resp_id = ids[1] or resp_id
        except (IndexError, TypeError):
            pass

        # Response text at inner[4][0][1][0]; choice ID at inner[4][0][0]
        try:
            entry = inner[4][0]
            text_list = entry[1]
            if text_list and isinstance(text_list[0], str):
                last_text = text_list[0]
                choice_id = entry[0] or choice_id
        except (IndexError, TypeError):
            pass

    return last_text, conv_id, resp_id, choice_id


def main() -> None:
    # Resolve cookie file path
    cookie_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "cookies.txt"

    print("=" * 50)
    print("  Gemini Web Chat")
    print("=" * 50)

    if not cookie_path.exists():
        print(f"Cookie file not found: {cookie_path}")
        print("Export your cookies from the browser as a Netscape cookie file")
        print("and save it as cookies.txt next to this script.")
        sys.exit(1)

    print(f"Loading cookies from: {cookie_path.name}")
    session = load_cookies(cookie_path)

    print("Connecting to Gemini...")
    try:
        at, bl = get_session_tokens(session)
    except requests.RequestException as e:
        print(f"Connection error: {e}")
        sys.exit(1)

    if not at:
        print("Failed to retrieve session token — cookies may be expired.")
        print("Export a fresh cookies.txt from your browser and try again.")
        sys.exit(1)

    print(f"Connected. (build: {bl})")
    print("\nType your message and press Enter. Type 'quit' to exit.\n")

    conv_id = resp_id = choice_id = ""

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        try:
            raw = send_message(session, user_input, at, bl, conv_id, resp_id, choice_id)
            text, new_conv, new_resp, new_choice = parse_response(raw)

            if text:
                print(f"\nGemini: {text}\n")
                conv_id = new_conv or conv_id
                resp_id = new_resp or resp_id
                choice_id = new_choice or choice_id
            else:
                print("Could not parse a response. First 300 chars of raw output:")
                print(raw[:300])
                print()

        except requests.HTTPError as e:
            print(f"HTTP {e.response.status_code}: {e}")
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
