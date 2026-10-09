# nrk-star-to-ytdlsub

Sync the shows you favourite ("merk") in NRK TV / NRK Radio to a
[ytdl-sub](https://github.com/jmbannon/ytdl-sub) `subscriptions.yaml`, so new
episodes end up in Jellyfin automatically.

```
[login] --token--> [sync] --subscriptions.yaml--> [ytdl-sub] --> Jellyfin
```

The tool only generates the subscriptions file; ytdl-sub does the downloading.
Only the favourites list needs authentication - the media itself is public
(geo-gated to Norway), so ytdl-sub needs no credentials.

## Install

```bash
uv sync
uv sync --extra login      # only needed for the interactive browser login
uv run playwright install chromium
```

## Log in

```bash
uv run nrk-star-to-ytdlsub login
```

A browser window opens; log in (BankID / Vipps / password). NRK's OAuth client
(`tv.nrk.no.web2`) is confidential — only NRK's backend can exchange the code —
so the tool lets the browser finish the flow and reads the session the web app
stores in `localStorage`. It keeps the NRK session cookies, which lets `sync`
mint fresh access tokens unattended until the browser session expires. Tokens
are stored in `~/.config/nrk-star-to-ytdlsub/token.json` (mode `600`).

Any Playwright browser works: `--browser firefox` / `webkit` (default
`chromium`).

No Playwright? Paste a bearer token from your browser's DevTools
(Network -> any `psapi.nrk.no` request -> `Authorization: Bearer ...`):

```bash
uv run nrk-star-to-ytdlsub login --access-token eyJ...
```

Note: this pasted token is short-lived and cannot be renewed.

## Sync

```bash
uv run nrk-star-to-ytdlsub sync --dry-run        # preview the YAML
uv run nrk-star-to-ytdlsub sync                  # write nrk_subs.yml
uv run nrk-star-to-ytdlsub status                # show stored token state
```

Useful flags: `--medium tv|radio|both`, `--out`, `--preset`, `--genre`,
`--tv-show-directory`, `--only-recent-days`.

## ytdl-sub

The generated file is intentionally minimal — it references one preset and lists
the shows:

```yaml
__preset__:
  output_options:
    maintain_download_archive: true
NRK TV Show:
  = NRK:
    Kongen av Gulset: https://tv.nrk.no/serie/kongen-av-gulset
```

Define `NRK TV Show` in your `config.yaml` (see below), then run ytdl-sub with
that config:

```bash
ytdl-sub -c /config/config.yaml sub /config/subscriptions.yaml /config/nrk_subs.yml
```

`maintain_download_archive: true` is set, so re-runs only fetch new episodes.
Un-favouriting a show just drops it from the file; already-downloaded files stay.

### Config preset (player-agnostic)

Keep everything player-specific in the preset and the subscriptions stay trivial.
Swap the top prebuilt preset to target another player (`Emby TV Show by Date`,
`Plex TV Show by Date`, ...):

```yaml
presets:
  "NRK TV Show":
    preset:
      - "Jellyfin TV Show by Date"   # <- swap for Emby/Plex
      - "Max 1080p"
      - "Filter Keywords"
      - "Only Recent"                # optional; enable to use only_recent_date_range
    overrides:
      tv_show_directory: "/tv_shows"
      only_recent_date_range: "30days"   # default; override with --only-recent-days
      title_exclude_keywords:
        - "TRAILER"
```

`only_recent_date_range` can live in the preset (config) *and/or* be emitted into
the subscription file with `--only-recent-days N`. Note ytdl-sub gives
`__preset__` overrides higher precedence than preset overrides, so the flag wins.

Then generate with the matching preset name / output file:

```bash
uv run nrk-star-to-ytdlsub sync --out nrk_subs.yml --preset "NRK TV Show"
```

## Run unattended (Docker + cron)

Run `sync` on a schedule next to ytdl-sub, sharing a volume:

```bash
nrk-star-to-ytdlsub sync --out /config/nrk_subs.yml \
  && ytdl-sub -c /config/config.yaml sub /config/subscriptions.yaml /config/nrk_subs.yml
```

Mount the token file as a secret (e.g. `/root/.config/nrk-star-to-ytdlsub/token.json`).
`sync` renews the access token from the stored session cookies; re-run `login`
(browser) only when the NRK session itself expires.

## How it works

- Auth: the browser completes NRK's OAuth login (`tv.nrk.no.web2` is a
  confidential client, so only NRK's backend can exchange the code); the CLI
  then reads the resulting session from the web app's `localStorage` and stores
  the access token plus the session cookies used to renew it.
- Favourites: `GET https://psapi.nrk.no/{tv,radio}/userdata/{userId}/favourites/manual`.
- URLs: `series` -> `/serie/<id>`, `programs` -> `/program/<id>`, `podcast` -> `/podkast/<id>`.

## Debugging in Helix

`debugpy` is a dev dependency and `.helix/languages.toml` configures it as a
debug adapter. Launch Helix **from the project root** (so `.venv/bin/python`
resolves):

```bash
uv sync --dev
hx .
```

Then, from any Python file:

| Keys           | Action                                            |
| -------------- | ------------------------------------------------- |
| `space G b`    | toggle a breakpoint on the current line           |
| `space G C-c`  | breakpoint condition                              |
| `space G C-l`  | breakpoint log message                            |
| `space G l`    | launch -> pick `nrk-login` / `nrk-sync` / `program` |
| `space G c`    | continue                                          |
| `space G n`    | step over                                         |
| `space G i`    | step into                                         |
| `space G o`    | step out                                          |
| `space G v`    | show variables                                    |
| `space G t`    | terminate                                         |

If Helix asks, run `:workspace-trust` once to allow the debug adapter.

Note: Helix's DAP client has open bugs with debugpy
([#6265](https://github.com/helix-editor/helix/issues/6265)), so it may drop the
session. If that happens, debug in the terminal instead: add a `breakpoint()` or
run `uv run python -m pdb -m nrk_star_to_ytdlsub login`. Break in
`src/nrk_star_to_ytdlsub/auth.py` inside the `_login_with_playwright` wait loop
to see what the browser is doing.

## Development

```bash
uv run pytest
```
