#!/usr/bin/env python3
"""
Gemini Chat API — Flask wrapper around the official Gemini API.

Setup:
  pip install flask google-generativeai python-dotenv Pillow

Run:
  python app.py

Endpoints:
  POST /chat
    Body (JSON):      { "message": "...", "conversation_id": "<optional uuid>" }
    Body (multipart): message=..., conversation_id=..., image=<file>
    Returns: { "reply": "...", "conversation_id": "<uuid>" }

  DELETE /chat/<conversation_id>
    Clears conversation history.

Environment variables:
  GEMINI_API_KEY  — your Gemini API key (required)
"""

import io
import os
import uuid

import google.generativeai as genai
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from PIL import Image

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_NAME = "gemini-2.0-flash"

# In-memory conversation store: { conversation_id -> list of Content (history) }
# Resets on cold starts in serverless environments.
conversations: dict = {}

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _configure_genai() -> None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
    genai.configure(api_key=key)


@app.post("/chat")
def chat():
    try:
        _configure_genai()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    if request.content_type and "multipart/form-data" in request.content_type:
        message = (request.form.get("message") or "").strip()
        convo_id = (request.form.get("conversation_id") or "").strip() or str(uuid.uuid4())
    else:
        body = request.get_json(silent=True) or {}
        message = (body.get("message") or "").strip()
        convo_id = (body.get("conversation_id") or "").strip() or str(uuid.uuid4())

    if not message:
        return jsonify({"error": "message is required"}), 400

    parts = [message]
    img_file = request.files.get("image")
    if img_file:
        parts.append(Image.open(io.BytesIO(img_file.read())))

    history = conversations.get(convo_id, [])
    session = genai.GenerativeModel(MODEL_NAME).start_chat(history=history)

    try:
        response = session.send_message(parts)
    except Exception as e:
        return jsonify({"error": f"Gemini error: {e}"}), 502

    conversations[convo_id] = session.history
    return jsonify({"reply": response.text, "conversation_id": convo_id})


@app.delete("/chat/<convo_id>")
def clear_conversation(convo_id: str):
    existed = conversations.pop(convo_id, None) is not None
    return jsonify({"cleared": existed, "conversation_id": convo_id})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
