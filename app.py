#!/usr/bin/env python3
"""
Gemini Web Chat — Flask API

Setup:
  pip install flask requests

Run:
  python app.py

Endpoints:
  POST /chat
    Body:    { "message": "...", "conversation_id": "<optional uuid>" }
    Returns: { "reply": "...", "conversation_id": "<uuid>" }

  DELETE /chat/<conversation_id>
    Clears a conversation so the next message starts fresh.
"""

import http.cookiejar
import json
import re
import uuid
from pathlib import Path

import requests
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

COOKIE_FILE = Path(__file__).parent / "cookies.txt"
BASE_URL = "https://gemini.google.com"
STREAM_URL = f"{BASE_URL}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
REQUEST_HEADERS = {
    "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    "referer": "https://gemini.google.com/",
    "x-same-domain": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/147.0.0.0 Safari/537.36"
    ),
}

# ---------------------------------------------------------------------------
# Gemini session (initialised once at startup)
# ---------------------------------------------------------------------------

gemini_session: requests.Session
at_token: str
bl_token: str

# In-memory conversation store: { conversation_id -> (conv_id, resp_id, choice_id) }
conversations: dict[str, tuple[str, str, str]] = {}


def init_gemini() -> None:
    global gemini_session, at_token, bl_token

    if not COOKIE_FILE.exists():
        raise FileNotFoundError(f"Cookie file not found: {COOKIE_FILE}")

    jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
    jar.load(ignore_discard=True, ignore_expires=True)

    gemini_session = requests.Session()
    gemini_session.cookies = jar  # type: ignore[assignment]

    resp = gemini_session.get(
        BASE_URL + "/",
        timeout=15,
        headers={"user-agent": REQUEST_HEADERS["user-agent"]},
    )
    resp.raise_for_status()

    at_match = re.search(r'"SNlM0e":"([^"]+)"', resp.text)
    bl_match = re.search(r'"cfb2h":"([^"]+)"', resp.text)

    if not at_match:
        raise RuntimeError("Could not extract session token — cookies may be expired.")

    at_token = at_match.group(1)
    bl_token = bl_match.group(1) if bl_match else "boq_assistant-bard-web-server_20260427.06_p4"
    print(f"Gemini session ready. build={bl_token}")


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

def _send(message: str, conv_id: str, resp_id: str, choice_id: str) -> str:
    inner = json.dumps([
        [message, 0, None, [], None, None, 0],
        ["en-GB"],
        [conv_id, resp_id, choice_id],
    ])
    f_req = json.dumps([None, inner])

    resp = gemini_session.post(
        STREAM_URL,
        params={"bl": bl_token, "hl": "en-GB", "_reqid": "1", "rt": "c"},
        data={"at": at_token, "f.req": f_req},
        headers=REQUEST_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def _parse(raw: str) -> tuple[str | None, str, str, str]:
    pattern = r'\["wrb\.fr",null,"((?:[^"\\]|\\.)*)"\]'
    last_text = None
    conv_id = resp_id = choice_id = ""

    for match in re.finditer(pattern, raw):
        try:
            inner_str = match.group(1).encode("utf-8").decode("unicode_escape")
            inner = json.loads(inner_str)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

        try:
            ids = inner[1]
            if isinstance(ids, list) and len(ids) >= 2:
                conv_id = ids[0] or conv_id
                resp_id = ids[1] or resp_id
        except (IndexError, TypeError):
            pass

        try:
            entry = inner[4][0]
            text_list = entry[1]
            if text_list and isinstance(text_list[0], str):
                last_text = text_list[0]
                choice_id = entry[0] or choice_id
        except (IndexError, TypeError):
            pass

    return last_text, conv_id, resp_id, choice_id


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.post("/chat")
def chat():
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    # Look up or create a conversation
    convo_id = (body.get("conversation_id") or "").strip() or str(uuid.uuid4())
    conv_id, resp_id, choice_id = conversations.get(convo_id, ("", "", ""))

    try:
        raw = _send(message, conv_id, resp_id, choice_id)
    except requests.HTTPError as e:
        return jsonify({"error": f"Gemini HTTP error: {e.response.status_code}"}), 502
    except requests.RequestException as e:
        return jsonify({"error": f"Request failed: {e}"}), 502

    text, new_conv, new_resp, new_choice = _parse(raw)

    if not text:
        return jsonify({"error": "No parseable reply from Gemini"}), 502

    # Persist updated conversation state
    conversations[convo_id] = (
        new_conv or conv_id,
        new_resp or resp_id,
        new_choice or choice_id,
    )

    return jsonify({"reply": text, "conversation_id": convo_id})


@app.delete("/chat/<convo_id>")
def clear_conversation(convo_id: str):
    existed = conversations.pop(convo_id, None) is not None
    return jsonify({"cleared": existed, "conversation_id": convo_id})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_gemini()
    app.run(debug=True, port=5000)
