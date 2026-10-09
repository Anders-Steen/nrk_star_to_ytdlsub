import time

import pytest

from nrk_star_to_ytdlsub import cli, nrk


@pytest.fixture
def fake_favourites(monkeypatch):
    def fake(self, user_id, medium="tv", *, content_group="adults", page_size=100):
        if medium == "tv":
            yield nrk.Favourite(
                "series",
                "kongen-av-gulset",
                "Kongen av Gulset",
                "https://tv.nrk.no/serie/kongen-av-gulset",
                "tv",
            )

    monkeypatch.setattr(nrk.NrkClient, "manual_favourites", fake)


def test_sync_dry_run(make_jwt, fake_favourites, capsys):
    token = make_jwt("11111111-2222-3333-4444-555555555555", int(time.time()) + 3600)
    rc = cli.main(
        ["sync", "--access-token", token, "--dry-run", "--medium", "tv"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "Kongen av Gulset" in out
    assert "https://tv.nrk.no/serie/kongen-av-gulset" in out


def test_sync_writes_file(make_jwt, fake_favourites, tmp_path):
    token = make_jwt("11111111-2222-3333-4444-555555555555", int(time.time()) + 3600)
    out = tmp_path / "subs.yaml"
    rc = cli.main(
        ["sync", "--access-token", token, "--medium", "tv", "--out", str(out)]
    )
    assert rc == 0
    assert out.is_file()
    assert "Kongen av Gulset" in out.read_text()


def test_login_browser_choice():
    args = cli.build_parser().parse_args(["login", "--browser", "firefox"])
    assert args.browser == "firefox"


def test_login_rejects_unknown_browser():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["login", "--browser", "netscape"])


def test_status_without_token_returns_error(tmp_path, capsys):
    rc = cli.main(["status", "--token-path", str(tmp_path / "missing.json")])
    assert rc == 1
    assert "No token" in capsys.readouterr().err


def test_login_access_token_then_status_and_sync(
    make_jwt, fake_favourites, tmp_path, capsys
):
    token_file = tmp_path / "token.json"
    token = make_jwt("11111111-2222-3333-4444-555555555555", int(time.time()) + 3600)

    assert cli.main(["login", "--access-token", token, "--token-path", str(token_file)]) == 0
    assert token_file.is_file()

    assert cli.main(["status", "--token-path", str(token_file)]) == 0
    assert "11111111-2222-3333-4444-555555555555" in capsys.readouterr().err

    assert (
        cli.main(
            ["sync", "--token-path", str(token_file), "--dry-run", "--medium", "tv"]
        )
        == 0
    )
    assert "Kongen av Gulset" in capsys.readouterr().out


def test_sync_reports_expired_token_without_refresh(make_jwt, tmp_path, capsys):
    token_file = tmp_path / "token.json"
    token = make_jwt("user", int(time.time()) - 10)  # already expired, no refresh token
    token_file.write_text(
        '{"sub": "user", "access_token": "%s", "refresh_token": null, "expires_at": 1, "scope": ""}'
        % token
    )
    rc = cli.main(["sync", "--token-path", str(token_file)])
    assert rc == 1
    assert "refresh" in capsys.readouterr().err.lower()
