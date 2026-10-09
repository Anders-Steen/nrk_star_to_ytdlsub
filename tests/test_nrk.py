import pytest

from nrk_star_to_ytdlsub import nrk


def _favourite(content_type: str, content_id: str, title: str, link: str) -> dict:
    return {
        "favouriteContentType": content_type,
        "favouriteContentId": content_id,
        "favouriteSource": "manual",
        "_links": {link: {"href": f"/{link}/{content_id}"}},
        "_embedded": {content_type: {"titles": {"title": title}}},
    }


def test_parse_series():
    fav = nrk.NrkClient._parse(
        _favourite("series", "bancroft", "Bancroft", "series"), "tv"
    )
    assert fav is not None
    assert fav.url == "https://tv.nrk.no/serie/bancroft"
    assert fav.title == "Bancroft"
    assert fav.content_type == "series"
    assert fav.source == "manual"


def test_parse_series_radio_uses_radio_host():
    fav = nrk.NrkClient._parse(
        _favourite("series", "dickie", "Dickie", "series"), "radio"
    )
    assert fav.url == "https://radio.nrk.no/serie/dickie"


def test_parse_program():
    fav = nrk.NrkClient._parse(
        _favourite("programs", "MDDP12000117", "Alarm Trolltunga", "programs"), "tv"
    )
    assert fav.url == "https://tv.nrk.no/program/MDDP12000117"


def test_parse_podcast():
    fav = nrk.NrkClient._parse(
        _favourite("podcast", "radioresepsjonens_arkivpodkast", "Arkivet", "podcasts"),
        "radio",
    )
    assert fav.url == "https://radio.nrk.no/podkast/radioresepsjonens_arkivpodkast"


def test_parse_uses_link_id_over_content_id():
    raw = _favourite("series", "ignored", "Show", "series")
    raw["_links"]["series"]["href"] = "/tv/catalog/series/from-the-link"
    fav = nrk.NrkClient._parse(raw, "tv")
    assert fav.url == "https://tv.nrk.no/serie/from-the-link"


def test_parse_unknown_type_returns_none():
    assert nrk.NrkClient._parse({"favouriteContentType": "movies"}, "tv") is None


class _FakeResponse:
    def __init__(self, data: dict, status_code: int = 200) -> None:
        self._data = data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._data


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, *, params=None, timeout=None) -> _FakeResponse:
        self.calls.append(url)
        return self._responses.pop(0)


def test_manual_favourites_follows_pagination():
    page_one = {
        "favourites": [_favourite("series", "one", "One", "series")],
        "_links": {"next": {"href": "/tv/userdata/u/favourites/manual?key=o1"}},
    }
    page_two = {
        "favourites": [_favourite("series", "two", "Two", "series")],
        "_links": {},
    }
    session = _FakeSession([_FakeResponse(page_one), _FakeResponse(page_two)])
    client = nrk.NrkClient("token", session=session)

    favourites = list(client.manual_favourites("u", "tv"))

    assert [f.title for f in favourites] == ["One", "Two"]
    assert session.calls[1].endswith("/tv/userdata/u/favourites/manual?key=o1")


def test_manual_favourites_raises_on_401():
    session = _FakeSession([_FakeResponse({}, status_code=401)])
    client = nrk.NrkClient("token", session=session)
    with pytest.raises(PermissionError):
        list(client.manual_favourites("u", "tv"))


def test_invalid_medium_raises():
    client = nrk.NrkClient("token", session=_FakeSession([]))
    with pytest.raises(ValueError):
        list(client.manual_favourites("u", "nonsense"))
