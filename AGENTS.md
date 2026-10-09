# AGENTS.md

`nrk-star-to-ytdlsub` is a Python CLI that syncs your NRK TV/Radio favourites
("merk") to a [ytdl-sub](https://github.com/jmbannon/ytdl-sub) subscriptions
file. It only generates the subscriptions file; ytdl-sub does the downloading.

## Commands

```bash
uv sync                     # runtime deps
uv sync --dev               # + pytest, ipython, debugpy
uv sync --extra login       # + playwright (interactive browser login)
uv run playwright install chromium

uv run pytest               # test suite
uv run python -m compileall -q src

uv run nrk-star-to-ytdlsub login --browser firefox
uv run nrk-star-to-ytdlsub login --access-token <jwt>   # no Playwright
uv run nrk-star-to-ytdlsub sync --dry-run
uv run nrk-star-to-ytdlsub sync --out nrk_subs.yml --preset "NRK TV Show"
uv run nrk-star-to-ytdlsub status
```

## Architecture

- `src/nrk_star_to_ytdlsub/auth.py` — NRK auth + token store.
- `src/nrk_star_to_ytdlsub/nrk.py` — psapi favourites client + URL mapping.
- `src/nrk_star_to_ytdlsub/ytdlsub.py` — minimal subscriptions YAML generator.
- `src/nrk_star_to_ytdlsub/cli.py` — argparse CLI (`login`/`sync`/`status`).
- `tests/` — pytest; Playwright tests skip when the browser isn't installed.

## Gotchas (things that have bitten us)

- `tv.nrk.no.web2` is a **confidential** OAuth client: only NRK's backend can
  exchange the code (`invalid_client` otherwise). Login is browser-assisted: the
  CLI lets the browser finish, reads the session from
  `localStorage["nrk-login-client-state"]`, and stores the NRK session cookies.
- `sync` renews the access token from those cookies
  (`/auth/csrf_init` + `/auth/session/tokenforsub/{sub}`). Re-run `login` only
  when the session itself expires.
- Do NOT add username/password login. It's fragile and stores secrets.
- Playwright's `page.route` does not intercept HTTP redirect targets — don't rely
  on it to capture the OAuth code.
- Helix's DAP client is buggy with debugpy (helix#6265); debug with `pdb` /
  `breakpoint()` instead.
- Generated `*.yml` / `*.yaml` are gitignored except `examples/`.

## Conventions

- Python >= 3.14, type hints, `from __future__ import annotations`.
- No comments except where the reasoning is non-obvious.
- Never commit secrets or `token.json`; `examples/ytdl-sub-config.yaml` is the
  only tracked YAML.
