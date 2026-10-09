"""Minimal client for the NRK Personal (psapi) favourites API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

import requests

PSAPI_BASE = "https://psapi.nrk.no"

# How each favourite content type maps to the HAL link relation and the
# embedded catalog payload.
_LINK_KEYS = {"series": "series", "programs": "programs", "podcast": "podcasts"}
_EMBED_KEYS = {"series": "series", "programs": "programs", "podcast": "podcast"}

_MEDIUM_HOST = {"tv": "tv.nrk.no", "radio": "radio.nrk.no"}
_URL_KIND = {"series": "serie", "programs": "program", "podcast": "podkast"}


@dataclass
class Favourite:
    content_type: str
    content_id: str
    title: str
    url: str
    medium: str
    source: str = "manual"


class NrkClient:
    def __init__(self, access_token: str, *, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "accept": "application/json",
            }
        )

    def _get(self, url: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.session.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            raise PermissionError("NRK returned 401 Unauthorized (token expired?)")
        resp.raise_for_status()
        return resp.json()

    def manual_favourites(
        self,
        user_id: str,
        medium: str = "tv",
        *,
        content_group: str | None = "adults",
        page_size: int = 100,
    ) -> Iterator[Favourite]:
        """Yield every manually marked favourite for one medium."""
        if medium not in _MEDIUM_HOST:
            raise ValueError(f"medium must be 'tv' or 'radio', got {medium!r}")

        url = f"{PSAPI_BASE}/{medium}/userdata/{user_id}/favourites/manual"
        params: dict[str, Any] | None = {"pageSize": page_size}
        if medium == "tv" and content_group:
            params["contentGroup"] = content_group

        while url:
            body = self._get(url, params=params)
            for raw in body.get("favourites", []):
                fav = self._parse(raw, medium)
                if fav is not None:
                    yield fav
            params = None
            nxt = (body.get("_links") or {}).get("next") or {}
            href = nxt.get("href")
            url = f"{PSAPI_BASE}{href}" if href else ""

    @staticmethod
    def _parse(raw: dict[str, Any], medium: str) -> Favourite | None:
        content_type = raw.get("favouriteContentType")
        if content_type not in _LINK_KEYS:
            return None

        content_id = str(raw.get("favouriteContentId", ""))
        link = ((raw.get("_links") or {}).get(_LINK_KEYS[content_type]) or {}).get("href")
        if link:
            content_id = link.rstrip("/").rsplit("/", 1)[-1]

        embedded = raw.get("_embedded") or {}
        catalog = embedded.get(_EMBED_KEYS[content_type]) or {}
        titles = catalog.get("titles") or {}
        title = titles.get("title") or titles.get("altTitle") or content_id

        host = _MEDIUM_HOST[medium]
        kind = _URL_KIND[content_type]
        url = f"https://{host}/{kind}/{content_id}"

        return Favourite(
            content_type=content_type,
            content_id=content_id,
            title=str(title),
            url=url,
            medium=medium,
            source=str(raw.get("favouriteSource", "manual")),
        )
