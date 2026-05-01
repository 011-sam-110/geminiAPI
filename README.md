# Gemini Web Chat API

A lightweight Python project that reverse-engineers the Gemini web interface to expose a clean REST API and an interactive CLI — built to deepen my understanding of HTTP traffic analysis, session management, and web API design.

---

## Overview

This project intercepts and replicates the network requests made by the Gemini web app, allowing programmatic access to Gemini without requiring an API key. It includes two interfaces:

- **`gemini_chat.py`** — an interactive terminal chat client
- **`app.py`** — a Flask REST API server that wraps the same logic

Both support multi-turn conversations with full context continuity across messages.

---

## Skills Demonstrated

- HTTP traffic analysis and request reverse-engineering
- Session and cookie management in Python (`http.cookiejar`, `requests`)
- Streaming response parsing (chunked JSON protocol)
- REST API design with Flask
- Stateful conversation management

---

## Project Structure

```
geminiAPI/
├── gemini_chat.py   # Interactive CLI chat client
├── app.py           # Flask REST API server
└── README.md
```

---

## Setup

**Requirements:** Python 3.10+

```bash
pip install flask requests
```

### Authentication

This project authenticates using your existing Google session cookies. To obtain them:

1. Log in to [gemini.google.com](https://gemini.google.com) in Chrome or Firefox
2. Install a browser extension such as [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
3. Export cookies for `google.com` in Netscape format
4. Save the file as `cookies.txt` in the project root

> **Note:** `cookies.txt` is excluded from version control via `.gitignore` — never commit session cookies to a public repository.

---

## Usage

### CLI Client

```bash
python gemini_chat.py
# or specify a custom cookie file:
python gemini_chat.py path/to/cookies.txt
```

```
==================================================
  Gemini Web Chat
==================================================
Loading cookies from: cookies.txt
Connecting to Gemini...
Connected. (build: boq_assistant-bard-web-server_...)

Type your message and press Enter. Type 'quit' to exit.

You: Explain recursion in simple terms
Gemini: Recursion is when a function calls itself to solve a smaller version
of the same problem...
```

### Flask API

Start the server:

```bash
python app.py
```

The server runs on `http://localhost:5000` by default.

#### `POST /chat`

Send a message. Omit `conversation_id` to start a new conversation.

```bash
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is machine learning?"}'
```

```json
{
  "reply": "Machine learning is a subset of artificial intelligence...",
  "conversation_id": "a3f12c9e-7d4b-4e1a-b5f0-1234567890ab"
}
```

#### Multi-turn conversation

Pass the returned `conversation_id` to continue a conversation with full context:

```bash
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Can you give me an example?", "conversation_id": "a3f12c9e-..."}'
```

#### `DELETE /chat/<conversation_id>`

Clear a conversation to start fresh:

```bash
curl -X DELETE http://localhost:5000/chat/a3f12c9e-...
```

```json
{
  "cleared": true,
  "conversation_id": "a3f12c9e-..."
}
```

---

## How It Works

1. **Cookie loading** — `http.cookiejar.MozillaCookieJar` loads the Netscape cookie file and attaches it to a `requests.Session`
2. **Token extraction** — the Gemini homepage is fetched once at startup to extract the CSRF token (`SNlM0e`) and build label (`cfb2h`) via regex
3. **Request construction** — user messages are embedded into the `f.req` form parameter, which follows Gemini's internal `StreamGenerate` protocol
4. **Response parsing** — the chunked streaming response is parsed with regex to extract `wrb.fr` JSON frames; the final frame contains the complete reply
5. **Conversation state** — `conv_id`, `resp_id`, and `choice_id` are tracked per conversation so each follow-up message carries the correct context

---

## Limitations

- Relies on Google's internal (undocumented) web API, which may change without notice
- Session cookies expire periodically — re-export `cookies.txt` when this happens
- In-memory conversation store is cleared on server restart; a persistent store (e.g. Redis, SQLite) could be added for production use

---

## License

MIT
