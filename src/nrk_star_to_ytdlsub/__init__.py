"""Sync NRK favourites to a ytdl-sub subscriptions file."""

from __future__ import annotations

from .cli import main as _main

__all__ = ["main"]


def main() -> int:
    """Console-script entry point."""
    return _main()
