"""NRK OAuth2 (Authorization Code + PKCE) login and token storage.

NRK authenticates against ``innlogging.nrk.no`` using a public OAuth2 client.
The web client id is ``tv.nrk.no.web2`` and the only registered redirect URI is
``https://tv.nrk.no/auth/signInCallback``, which is served by NRK and consumes
the authorization code server-side.  A CLI therefore cannot simply receive the
code on a local callback: we drive a real browser and intercept the navigation
to the redirect URI, aborting it before NRK can consume the code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

AUTHORITY = "https://innlogging.nrk.no"
AUTHORIZE_ENDPOINT = f"{AUTHORITY}/connect/authorize"
TOKEN_ENDPOINT = f"{AUTHORITY}/connect/token"
USERINFO_ENDPOINT = f"{AUTHORITY}/connect/userinfo"

CLIENT_ID = "tv.nrk.no.web2"
REDIRECT_URI = "https://tv.nrk.no/auth/signInCallback"
SCOPE = "openid profile email psapi-userdata nrkno-userdata offline_access"

# tv.nrk.no.web2 is a *confidential* client: only NRK's backend holds the
# secret and performs the code exchange. We therefore let the browser complete
# the login and read the session the web app stores in localStorage.
TV_BASE = "https://tv.nrk.no"
TV_LOGIN_URL = f"{TV_BASE}/auth/web/login"
SESSION_STATE_KEY = "nrk-login-client-state"

# Refresh a little before actual expiry to avoid racing the clock.
EXPIRY_SKEW_SECONDS = 60


class AuthError(RuntimeError):
    """Raised when login, refresh, or token storage fails."""


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / "nrk-star-to-ytdlsub"


def default_token_path() -> Path:
    return config_dir() / "token.json"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for the S256 method."""
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(code_challenge: str, state: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
    except IndexError as exc:  # not a JWT
        raise AuthError("token is not a JWT") from exc
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@dataclass
class Tokens:
    sub: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float = 0.0
    scope: str = ""
    cookies: dict[str, str] | None = None

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - EXPIRY_SKEW_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub": self.sub,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "cookies": self.cookies,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tokens":
        return cls(
            sub=data["sub"],
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=float(data.get("expires_at", 0.0)),
            scope=data.get("scope", ""),
            cookies=data.get("cookies"),
        )


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_token_path()

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Tokens:
        if not self.exists():
            raise AuthError(f"no token at {self.path}; run `login` first")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return Tokens.from_dict(data)

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(tokens.to_dict(), indent=2)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def _tokens_from_response(data: dict[str, Any], previous: Tokens | None = None) -> Tokens:
    access_token = data["access_token"]
    expires_at = time.time() + float(data.get("expires_in", 0) or 0)
    if not data.get("expires_in"):
        try:
            expires_at = float(_decode_jwt_payload(access_token).get("exp", 0))
        except AuthError:
            expires_at = time.time() + 3600

    sub = (previous.sub if previous else "") or ""
    try:
        sub = sub or str(_decode_jwt_payload(access_token).get("sub", ""))
    except AuthError:
        pass

    return Tokens(
        sub=sub,
        access_token=access_token,
        refresh_token=data.get("refresh_token") or (previous.refresh_token if previous else None),
        expires_at=expires_at,
        scope=data.get("scope", previous.scope if previous else ""),
    )


def _resolve_sub(tokens: Tokens) -> str:
    if tokens.sub:
        return tokens.sub
    try:
        sub = _decode_jwt_payload(tokens.access_token).get("sub")
        if sub:
            return str(sub)
    except AuthError:
        pass
    resp = requests.get(
        USERINFO_ENDPOINT,
        headers={"Authorization": f"Bearer {tokens.access_token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return str(resp.json()["sub"])


def exchange_code(
    code: str,
    code_verifier: str,
    *,
    session: requests.Session | None = None,
) -> Tokens:
    http = session or requests
    resp = http.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    _raise_for_token_error(resp)
    tokens = _tokens_from_response(resp.json())
    tokens.sub = _resolve_sub(tokens)
    return tokens


def refresh(tokens: Tokens, *, session: requests.Session | None = None) -> Tokens:
    if not tokens.refresh_token:
        raise AuthError("token has no refresh_token; run `login` again")
    http = session or requests
    resp = http.post(
        TOKEN_ENDPOINT,
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": tokens.refresh_token,
        },
        timeout=30,
    )
    _raise_for_token_error(resp)
    refreshed = _tokens_from_response(resp.json(), previous=tokens)
    if not refreshed.sub:
        refreshed.sub = _resolve_sub(refreshed)
    return refreshed


def import_access_token(access_token: str) -> Tokens:
    """Build a token store entry from a raw bearer token (browser-assisted)."""
    try:
        payload = _decode_jwt_payload(access_token)
        expires_at = float(payload.get("exp", 0))
        sub = str(payload.get("sub", ""))
    except AuthError:
        expires_at = 0.0
        sub = ""
    tokens = Tokens(sub=sub, access_token=access_token, expires_at=expires_at)
    tokens.sub = _resolve_sub(tokens) if not sub else sub
    return tokens


def _raise_for_token_error(resp: requests.Response) -> None:
    if resp.ok:
        return
    try:
        detail = resp.json()
    except ValueError:
        detail = resp.text
    raise AuthError(f"token endpoint returned {resp.status_code}: {detail}")


SUPPORTED_BROWSERS = ("chromium", "firefox", "webkit")


def _login_with_playwright(
    login_url: str,
    *,
    browser_name: str = "chromium",
    timeout_seconds: int = 300,
    headless: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Open a browser, let the user log in, and return the session the app stores.

    Returns ``(session_state, cookies)`` where ``session_state`` is the parsed
    ``localStorage["nrk-login-client-state"]`` (contains ``sessionData`` with an
    access token) and ``cookies`` are the NRK session cookies.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise AuthError(
            "Playwright is required for the interactive browser login.\n"
            "Install it with:  uv sync --extra login\n"
            "then:             uv run playwright install chromium\n\n"
            "Alternatively, paste a token from your browser with "
            "`login --access-token <jwt>`."
        ) from exc

    if browser_name not in SUPPORTED_BROWSERS:
        raise AuthError(
            f"unsupported browser {browser_name!r}; choose one of "
            f"{', '.join(SUPPORTED_BROWSERS)}"
        )

    session_state: dict[str, Any] = {}
    cookies: dict[str, str] = {}

    with sync_playwright() as playwright:
        try:
            browser = getattr(playwright, browser_name).launch(headless=headless)
        except PlaywrightError as exc:
            raise AuthError(
                f"Could not launch the {browser_name} browser for login:\n"
                f"  {exc}\n\n"
                f"Install it with:  uv run playwright install {browser_name}"
            ) from exc

        context = browser.new_context()
        page = context.new_page()

        try:
            page.goto(login_url)
        except PlaywrightError:
            pass

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                raw = page.evaluate(
                    f"() => localStorage.getItem({SESSION_STATE_KEY!r})"
                )
            except PlaywrightError:
                raw = None
            if raw:
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict) and (parsed.get("sessionData") or {}).get(
                    "accessToken"
                ):
                    session_state = parsed
                    break
            page.wait_for_timeout(500)

        cookies = {
            c["name"]: c["value"]
            for c in context.cookies()
            if "nrk.no" in c.get("domain", "")
        }
        context.close()
        browser.close()

    if not session_state:
        raise AuthError(
            "login timed out or was cancelled before NRK established a session"
        )
    return session_state, cookies


def _expires_at(session_state: dict[str, Any], access_token: str) -> float:
    """Best-effort access-token expiry as a UNIX timestamp (seconds)."""
    try:
        exp = _decode_jwt_payload(access_token).get("exp")
        if exp:
            return float(exp)
    except AuthError:
        pass
    expires = session_state.get("expires")
    if expires:
        # The web client stores ``Date.now() + ms``.
        return float(expires) / 1000
    return time.time() + 3600


def tokens_from_session_state(
    session_state: dict[str, Any], cookies: dict[str, str] | None = None
) -> Tokens:
    session_data = session_state.get("sessionData") or {}
    access_token = session_data.get("accessToken")
    if not access_token:
        raise AuthError("no access token found in the NRK browser session")
    sub = str((session_data.get("user") or {}).get("sub") or "")
    if not sub:
        raise AuthError("no user id found in the NRK browser session")
    return Tokens(
        sub=sub,
        access_token=access_token,
        expires_at=_expires_at(session_state, access_token),
        cookies=cookies or None,
    )


def mint_from_cookies(
    tokens: Tokens, *, session: requests.Session | None = None
) -> Tokens:
    """Mint a fresh access token from the stored NRK session cookies.

    The web session (cookies) outlives the access token, so this is what lets
    `sync` run unattended for as long as the browser session stays valid.
    """
    if not tokens.cookies:
        raise AuthError("no stored session cookies; run `login` again")

    http = session or requests.Session()
    http.cookies.update(tokens.cookies)
    common = {"nrk-fetch": "1", "accept": "application/json"}

    csrf_resp = http.post(f"{TV_BASE}/auth/csrf_init", headers=common, timeout=30)
    headers = dict(common)
    csrf = csrf_resp.headers.get("nrk-proxy-csrf")
    if csrf:
        headers["nrk-proxy-csrf"] = csrf

    resp = http.post(
        f"{TV_BASE}/auth/session/tokenforsub/{tokens.sub}",
        headers=headers,
        timeout=30,
    )
    if not resp.ok:
        raise AuthError(
            f"could not refresh NRK token ({resp.status_code}); run `login` again"
        )
    data = resp.json()
    fresh = tokens_from_session_state(
        data, cookies=dict(http.cookies.get_dict()) or tokens.cookies
    )
    return fresh


def interactive_login(
    *,
    store: TokenStore,
    browser: str = "chromium",
    timeout_seconds: int = 300,
) -> Tokens:
    session_state, cookies = _login_with_playwright(
        TV_LOGIN_URL, browser_name=browser, timeout_seconds=timeout_seconds
    )
    tokens = tokens_from_session_state(session_state, cookies)
    store.save(tokens)
    return tokens
