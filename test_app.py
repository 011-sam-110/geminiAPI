"""
Integration tests against the live Vercel deployment.

Run:
  python test_app.py
  # or
  python -m pytest test_app.py -v
"""

import unittest
import urllib.request
import urllib.error
import json
from pathlib import Path

BASE_URL = "https://gemini-api-three-kappa.vercel.app"

HERE = Path(__file__).parent
SCREENSHOT_1 = HERE / "Screenshot 2026-05-03 223422.png"
SCREENSHOT_2 = HERE / "Screenshot 2026-05-03 225424.png"


def _parse_response(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw.decode(errors="replace")}


def _post_json(path: str, payload: dict, timeout: int = 30) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _parse_response(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_response(e.read())


def _post_multipart(path: str, fields: dict, files: dict, timeout: int = 30) -> tuple[int, dict]:
    """Encode and POST multipart/form-data. files = {name: (filename, data, mime)}."""
    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    body_parts = []

    for name, value in fields.items():
        body_parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f'{value}\r\n'
        )

    for name, (filename, data, mime) in files.items():
        header = (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f'Content-Type: {mime}\r\n\r\n'
        )
        body_parts.append(header.encode() + data + b'\r\n')

    body_parts.append(f'--{boundary}--\r\n')

    encoded = b"".join(
        p.encode() if isinstance(p, str) else p for p in body_parts
    )

    req = urllib.request.Request(
        BASE_URL + path,
        data=encoded,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _parse_response(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_response(e.read())


def _delete(path: str, timeout: int = 10) -> tuple[int, dict]:
    req = urllib.request.Request(BASE_URL + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _parse_response(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_response(e.read())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestChatTextOnly(unittest.TestCase):

    def test_basic_message_gets_reply(self):
        status, data = _post_json("/chat", {"message": "Reply with only the word: pineapple"})
        self.assertEqual(status, 200, data)
        self.assertIn("reply", data)
        self.assertIn("conversation_id", data)
        print(f"\n  reply: {data['reply'][:120]}")

    def test_conversation_id_is_reused(self):
        status1, data1 = _post_json("/chat", {"message": "My secret number is 7. Remember it."})
        self.assertEqual(status1, 200, data1)
        convo_id = data1["conversation_id"]

        status2, data2 = _post_json("/chat", {
            "message": "What was my secret number?",
            "conversation_id": convo_id,
        })
        self.assertEqual(status2, 200, data2)
        self.assertEqual(data2["conversation_id"], convo_id)
        print(f"\n  follow-up reply: {data2['reply'][:120]}")

    def test_missing_message_returns_400(self):
        status, data = _post_json("/chat", {})
        self.assertEqual(status, 400, data)
        self.assertIn("error", data)

    def test_empty_message_returns_400(self):
        status, data = _post_json("/chat", {"message": "   "})
        self.assertEqual(status, 400, data)


class TestChatWithImage(unittest.TestCase):

    def test_screenshot_1_gets_reply(self):
        image_data = SCREENSHOT_1.read_bytes()
        status, data = _post_multipart(
            "/chat",
            fields={"message": "Describe what you see in this screenshot in one sentence."},
            files={"image": (SCREENSHOT_1.name, image_data, "image/png")},
            timeout=90,
        )
        self.assertEqual(status, 200, data)
        self.assertIn("reply", data)
        print(f"\n  screenshot 1 reply: {data['reply'][:120]}")

    def test_screenshot_2_gets_reply(self):
        image_data = SCREENSHOT_2.read_bytes()
        status, data = _post_multipart(
            "/chat",
            fields={"message": "Describe what you see in this screenshot in one sentence."},
            files={"image": (SCREENSHOT_2.name, image_data, "image/png")},
            timeout=90,
        )
        self.assertEqual(status, 200, data)
        self.assertIn("reply", data)
        print(f"\n  screenshot 2 reply: {data['reply'][:120]}")

    def test_multipart_without_image_still_works(self):
        status, data = _post_multipart(
            "/chat",
            fields={"message": "Reply with only the word: mango"},
            files={},
        )
        self.assertEqual(status, 200, data)
        self.assertIn("reply", data)

    def test_multipart_missing_message_returns_400(self):
        status, data = _post_multipart("/chat", fields={}, files={})
        self.assertEqual(status, 400, data)


class TestDeleteConversation(unittest.TestCase):

    def test_delete_existing_conversation(self):
        _, data = _post_json("/chat", {"message": "Hello"})
        convo_id = data["conversation_id"]

        status, del_data = _delete(f"/chat/{convo_id}")
        self.assertEqual(status, 200, del_data)
        self.assertTrue(del_data["cleared"])
        self.assertEqual(del_data["conversation_id"], convo_id)

    def test_delete_nonexistent_returns_cleared_false(self):
        status, data = _delete("/chat/00000000-does-not-exist")
        self.assertEqual(status, 200, data)
        self.assertFalse(data["cleared"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
