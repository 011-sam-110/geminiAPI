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

Environment variables:
  GEMINI_COOKIES  — contents of a Netscape-format cookie file (required on Vercel)
"""

import hashlib
import http.cookiejar
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, jsonify, request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://gemini.google.com"
STREAM_URL = f"{BASE_URL}/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
UPLOAD_URL = "https://content-push.googleapis.com/upload/"
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
# Gemini session (lazily initialised on first request)
# ---------------------------------------------------------------------------

gemini_session: requests.Session | None = None
at_token: str = ""
bl_token: str = ""

# In-memory conversation store: { conversation_id -> (conv_id, resp_id, choice_id) }
# Note: this resets on cold starts in serverless environments.
conversations: dict[str, tuple[str, str, str]] = {}


def _cookie_file() -> Path:
    """Return a path to a cookie file, writing from GEMINI_COOKIES env var if set."""
    env_cookies = os.environ.get("GEMINI_COOKIES", "")
    if env_cookies:
        tmp = Path(tempfile.gettempdir()) / "gemini_cookies.txt"
        tmp.write_text(env_cookies)
        return tmp
    return Path(__file__).parent / "cookies.txt"


def init_gemini() -> None:
    global gemini_session, at_token, bl_token

    cookie_file = _cookie_file()
    if not cookie_file.exists():
        raise FileNotFoundError(
            "Cookie file not found. Set the GEMINI_COOKIES environment variable "
            "to the contents of a Netscape-format cookie file."
        )

    jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
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


def _ensure_initialized() -> None:
    """Lazily initialise the Gemini session on the first request."""
    if gemini_session is None:
        init_gemini()


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

def _sapisid_hash(origin: str) -> str:
    """Compute the SAPISIDHASH Authorization value required by Google upload APIs."""
    sapisid = next(
        (c.value for c in gemini_session.cookies if c.name in ("SAPISID", "__Secure-3PAPISID")),
        "",
    )
    ts = str(int(time.time()))
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def _upload_image(image_data: bytes, mime_type: str) -> str:
    """Upload an image via Google's two-step resumable upload and return the image path."""
    auth = _sapisid_hash("https://gemini.google.com")
    # Cookies must be sent explicitly — different domain from gemini.google.com.
    cookie_header = "; ".join(
        f"{c.name}={c.value}" for c in gemini_session.cookies
    )
    common = {
        "Authorization": auth,
        "Cookie": cookie_header,
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
    }

    # Step 1: initiate — tells Google the upload size/type, gets back an upload URL.
    r1 = requests.post(
        UPLOAD_URL,
        headers={
            **common,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(image_data)),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        },
        json={},
        timeout=15,
    )
    r1.raise_for_status()
    upload_url = r1.headers.get("X-Goog-Upload-URL", UPLOAD_URL)

    # Step 2: send the image bytes to the returned upload URL.
    r2 = requests.post(
        upload_url,
        headers={
            **common,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Offset": "0",
            "Content-Type": mime_type,
        },
        data=image_data,
        timeout=45,
    )
    r2.raise_for_status()
    return r2.text.strip()


def _send(
    message: str,
    conv_id: str,
    resp_id: str,
    choice_id: str,
    image_path: str = "",
    mime_type: str = "",
    filename: str = "",
) -> str:
    image_list = [[[image_path, 1, None, mime_type], filename]] if image_path else []
    inner = json.dumps([
        [message, 0, None, image_list, None, None, 0],
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
    try:
        _ensure_initialized()
    except Exception as e:
        return jsonify({"error": f"Initialisation failed: {e}"}), 503

    if request.content_type and "multipart/form-data" in request.content_type:
        message = (request.form.get("message") or "").strip()
        convo_id = (request.form.get("conversation_id") or "").strip() or str(uuid.uuid4())
    else:
        body = request.get_json(silent=True) or {}
        message = (body.get("message") or "").strip()
        convo_id = (body.get("conversation_id") or "").strip() or str(uuid.uuid4())

    if not message:
        return jsonify({"error": "message is required"}), 400

    image_path = mime_type = filename = ""
    img_file = request.files.get("image")
    if img_file:
        try:
            image_path = _upload_image(img_file.read(), img_file.mimetype or "image/jpeg")
            mime_type = img_file.mimetype or "image/jpeg"
            filename = img_file.filename or "image.jpg"
        except requests.HTTPError as e:
            return jsonify({"error": f"Image upload failed: {e.response.status_code}"}), 502
        except requests.RequestException as e:
            return jsonify({"error": f"Image upload failed: {e}"}), 502

    # Look up or create a conversation
    conv_id, resp_id, choice_id = conversations.get(convo_id, ("", "", ""))

    try:
        raw = _send(message, conv_id, resp_id, choice_id, image_path, mime_type, filename)
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

# Vercel looks for a module-level `app` variable — it's already defined above.

