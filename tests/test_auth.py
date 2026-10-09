import os
import time

import pytest

from nrk_star_to_ytdlsub import auth


def test_generate_pkce():
    verifier, challenge = auth.generate_pkce()
    assert len(verifier) >= 43
    assert len(challenge) == 43
    assert "=" not in verifier and "=" not in challenge

    another, _ = auth.generate_pkce()
    assert another != verifier


def test_build_authorize_url():
    url = auth.build_authorize_url("challenge-value", "state-value")
    assert url.startswith(auth.AUTHORIZE_ENDPOINT + "?")
    assert "client_id=tv.nrk.no.web2" in url
    assert "response_type=code" in url
    assert "code_challenge=challenge-value" in url
    assert "code_challenge_method=S256" in url
    assert "offline_access" in url
    assert "state=state-value" in url


def test_token_store_roundtrip(tmp_path):
    store = auth.TokenStore(tmp_path / "token.json")
    assert not store.exists()

    tokens = auth.Tokens(
        sub="user-1",
        access_token="access",
        refresh_token="refresh",
        expires_at=time.time() + 3600,
        scope="openid psapi-userdata",
    )
    store.save(tokens)

    assert store.exists()
    assert store.load() == tokens
    assert os.stat(store.path).st_mode & 0o777 == 0o600


def test_token_store_missing_raises(tmp_path):
    store = auth.TokenStore(tmp_path / "nope.json")
    with pytest.raises(auth.AuthError):
        store.load()


def test_import_access_token(make_jwt):
    token = make_jwt("user-123", int(time.time()) + 3600)
    tokens = auth.import_access_token(token)
    assert tokens.sub == "user-123"
    assert not tokens.is_expired
    assert tokens.refresh_token is None


def test_token_expiry():
    assert auth.Tokens("u", "a", expires_at=time.time() - 1).is_expired
    assert not auth.Tokens("u", "a", expires_at=time.time() + 3600).is_expired
    # refresh skew: a token expiring in 10s is treated as expired
    assert auth.Tokens("u", "a", expires_at=time.time() + 10).is_expired


def test_decode_jwt_payload_rejects_non_jwt():
    with pytest.raises(auth.AuthError):
        auth._decode_jwt_payload("not-a-jwt")
