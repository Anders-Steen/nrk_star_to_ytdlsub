import base64
import json

import pytest


def _encode(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


@pytest.fixture
def make_jwt():
    """Return a factory for unsigned JWTs with a sub/exp, for decoding tests."""

    def factory(sub: str, exp: int) -> str:
        return f"{_encode({'alg': 'RS256'})}.{_encode({'sub': sub, 'exp': exp})}.sig"

    return factory
