"""Command line interface for nrk-star-to-ytdlsub."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import auth, nrk, ytdlsub


def _eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def _token_path(args: argparse.Namespace) -> Path:
    return Path(args.token_path).expanduser() if args.token_path else auth.default_token_path()


def _active_tokens(args: argparse.Namespace) -> auth.Tokens:
    """Return a usable access token, refreshing or importing as needed."""
    store = auth.TokenStore(_token_path(args))

    if getattr(args, "access_token", None):
        return auth.import_access_token(args.access_token)

    tokens = store.load()
    if tokens.is_expired:
        if tokens.refresh_token:
            tokens = auth.refresh(tokens)
        elif tokens.cookies:
            tokens = auth.mint_from_cookies(tokens)
        else:
            raise auth.AuthError(
                "stored token has expired and there is no way to refresh it; "
                "run `login` again"
            )
        store.save(tokens)
    return tokens


def _cmd_login(args: argparse.Namespace) -> int:
    store = auth.TokenStore(_token_path(args))

    if args.access_token:
        tokens = auth.import_access_token(args.access_token)
        store.save(tokens)
        _eprint(f"Saved browser token for user {tokens.sub} (expires in "
                f"{int(tokens.expires_at - time.time())}s; no refresh token).")
        return 0

    tokens = auth.interactive_login(
        store=store, browser=args.browser, timeout_seconds=args.timeout
    )
    renew = "session cookies" if tokens.cookies else "none"
    _eprint(f"Logged in as {tokens.sub}.")
    _eprint(f"Token saved to {store.path} (renewable via: {renew}).")
    if not tokens.cookies:
        _eprint("WARNING: no session cookies captured; `sync` will need a fresh "
                "login when the access token expires.")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    tokens = _active_tokens(args)
    client = nrk.NrkClient(tokens.access_token)

    mediums = ["tv", "radio"] if args.medium == "both" else [args.medium]
    favourites: list[nrk.Favourite] = []
    for medium in mediums:
        group = args.content_group if medium == "tv" else None
        found = list(
            client.manual_favourites(tokens.sub, medium, content_group=group)
        )
        _eprint(f"Found {len(found)} manual {medium} favourites.")
        favourites.extend(found)

    if not favourites:
        _eprint("Nothing to do - no manual favourites.")
        return 0

    document = ytdlsub.build_subscription_data(
        favourites,
        preset=args.preset,
        tv_show_directory=args.tv_show_directory,
        genre=args.genre,
        only_recent_days=args.only_recent_days,
    )
    content = ytdlsub.dump_subscriptions(document)

    if args.dry_run:
        sys.stdout.write(content)
        return 0

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, out)
    _eprint(f"Wrote {len(favourites)} subscriptions to {out}.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = auth.TokenStore(_token_path(args))
    if not store.exists():
        _eprint(f"No token at {store.path}. Run `login`.")
        return 1
    tokens = store.load()
    remaining = int(tokens.expires_at - time.time())
    _eprint(f"Token file:     {store.path}")
    _eprint(f"User (sub):     {tokens.sub}")
    _eprint(f"Renewable:      {'session cookies' if tokens.cookies else ('refresh token' if tokens.refresh_token else 'no')}")
    _eprint(f"Access expires: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(tokens.expires_at))} "
            f"({remaining}s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nrk-star-to-ytdlsub",
        description="Sync your NRK TV/Radio favourites to a ytdl-sub subscriptions file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="log in to NRK and store OAuth tokens")
    login.add_argument("--access-token", help="import a raw bearer token instead of a browser login")
    login.add_argument("--browser", choices=auth.SUPPORTED_BROWSERS, default="chromium",
                       help="Playwright browser to drive (default: chromium)")
    login.add_argument("--token-path", help="override token file location")
    login.add_argument("--timeout", type=int, default=300,
                       help="seconds to wait for the browser login (default 300)")
    login.set_defaults(func=_cmd_login)

    sync = sub.add_parser("sync", help="write the ytdl-sub subscriptions file")
    sync.add_argument("--out", default="nrk_subs.yml",
                      help="output file (default: nrk_subs.yml)")
    sync.add_argument("--preset", default=ytdlsub.DEFAULT_PRESET,
                      help=f"preset to group the subscriptions under; define it in "
                           f"your config.yaml (default: {ytdlsub.DEFAULT_PRESET!r})")
    sync.add_argument("--tv-show-directory", default=None,
                      help="tv_show_directory override (only set if your preset doesn't)")
    sync.add_argument("--only-recent-days", type=int, default=None,
                      help="write only_recent_date_range=Ndays into the file "
                           "(requires the 'Only Recent' preset)")
    sync.add_argument("--genre", default="NRK", help="genre tag to add (empty to disable)")
    sync.add_argument("--medium", choices=["tv", "radio", "both"], default="both")
    sync.add_argument("--content-group", default="adults", help="tv content group (adults/children)")
    sync.add_argument("--token-path", help="override token file location")
    sync.add_argument("--access-token", help="use this bearer token for a one-off run")
    sync.add_argument("--dry-run", action="store_true", help="print YAML instead of writing")
    sync.set_defaults(func=_cmd_sync)

    status = sub.add_parser("status", help="show the stored token state")
    status.add_argument("--token-path", help="override token file location")
    status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (auth.AuthError, PermissionError, ValueError) as exc:
        _eprint(f"error: {exc}")
        return 1
