"""Tests for the browser-assisted login and session-cookie renewal."""

import http.server
import json
import socketserver
import threading
import time

import pytest
import requests

from nrk_star_to_ytdlsub import auth

STATE = {
    "sessionData": {"accessToken": "TOK", "user": {"sub": "user-1"}},
    "expires": 9999999999999,
}
_PAGE = (
    "<html><body><script>"
    f"localStorage.setItem({json.dumps(auth.SESSION_STATE_KEY)}, "
    f"{json.dumps(json.dumps(STATE))});"
    "</script></body></html>"
)


class _PageHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = _PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def test_login_reads_session_state_from_browser():
    pytest.importorskip("playwright.sync_api")
    with socketserver.TCPServer(("127.0.0.1", 0), _PageHandler) as server:
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            state, _cookies = auth._login_with_playwright(
                f"http://127.0.0.1:{port}/login", headless=True, timeout_seconds=20
            )
        except auth.AuthError as exc:  # browser not installed in this environment
            pytest.skip(f"browser unavailable: {exc}")
        finally:
            server.shutdown()

    assert state["sessionData"]["accessToken"] == "TOK"


def test_tokens_from_session_state(make_jwt):
    token = make_jwt("user-9", int(time.time()) + 3600)
    state = {"sessionData": {"accessToken": token, "user": {"sub": "user-9"}}}
    tokens = auth.tokens_from_session_state(state, {"sid": "abc"})
    assert tokens.sub == "user-9"
    assert tokens.access_token == token
    assert tokens.cookies == {"sid": "abc"}
    assert not tokens.is_expired


def test_tokens_from_session_state_requires_token():
    with pytest.raises(auth.AuthError, match="access token"):
        auth.tokens_from_session_state({"sessionData": {"user": {"sub": "u"}}})


class _FakeResponse:
    def __init__(self, ok=True, status_code=200, headers=None, data=None):
        self.ok = ok
        self.status_code = status_code
        self.headers = headers or {}
        self._data = data or {}

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, responses):
        self.cookies = requests.cookies.RequestsCookieJar()
        self._responses = responses
        self.calls = []

    def post(self, url, headers=None, timeout=None):
        self.calls.append((url, headers or {}))
        return self._responses.pop(0)


def test_mint_from_cookies(make_jwt):
    token = make_jwt("u1", int(time.time()) + 3600)
    session = _FakeSession(
        [
            _FakeResponse(headers={"nrk-proxy-csrf": "csrf-token"}),
            _FakeResponse(
                data={
                    "sessionData": {"accessToken": token, "user": {"sub": "u1"}},
                    "expires": 0,
                }
            ),
        ]
    )
    old = auth.Tokens(
        sub="u1", access_token="old", expires_at=time.time() - 1, cookies={"sid": "x"}
    )

    fresh = auth.mint_from_cookies(old, session=session)

    assert fresh.access_token == token
    assert fresh.sub == "u1"
    assert not fresh.is_expired
    assert session.calls[0][0].endswith("/auth/csrf_init")
    assert session.calls[1][0].endswith("/auth/session/tokenforsub/u1")
    assert session.calls[1][1]["nrk-proxy-csrf"] == "csrf-token"


def test_mint_from_cookies_without_cookies_raises():
    tokens = auth.Tokens(sub="u1", access_token="a", expires_at=0)
    with pytest.raises(auth.AuthError, match="session cookies"):
        auth.mint_from_cookies(tokens, session=_FakeSession([]))
