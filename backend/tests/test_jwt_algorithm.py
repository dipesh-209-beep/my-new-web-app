"""AUD-004: the JWT decoder must fail closed when JWT_ALGORITHM is set
outside the allowlist, even outside production.

No database is needed: with a disallowed algorithm the effective allowlist
is empty, so jwt.decode rejects the token before any admin/user row is
looked up (the `db=None` below is never dereferenced).

The production-side refusal lives in tests/test_config.py.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest

from app.core import security
from app.core.config import JWT_ALLOWED_ALGORITHMS


def _fake_settings(algorithm: str) -> SimpleNamespace:
    return SimpleNamespace(jwt_secret_key="k" * 40, jwt_algorithm=algorithm)


def _token(algorithm: str = "HS256", key: object = "k" * 40) -> str:
    return jwt.encode(
        {
            "sub": "1",
            "type": "admin",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        key,
        algorithm=algorithm,
    )


def test_allowlist_is_the_symmetric_hs_family():
    assert JWT_ALLOWED_ALGORITHMS == frozenset({"HS256", "HS384", "HS512"})


def test_unsigned_token_is_rejected_when_algorithm_is_none(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: _fake_settings("none"))
    # An attacker-minted unsigned token: alg=none, no signature.
    unsigned = _token(algorithm="none", key=None)

    assert security._decode_admin_token(unsigned, db=None) is None
    assert security._decode_user_token(unsigned, db=None) is None


@pytest.mark.parametrize("algorithm", ["none", "RS256", "ES256", "hs256"])
def test_disallowed_configuration_rejects_every_token(monkeypatch, algorithm):
    """Whatever JWT_ALGORITHM says, a value outside the allowlist must make
    the decoder accept nothing -- not even a token that is otherwise
    validly HMAC-signed with the real secret."""
    monkeypatch.setattr(security, "get_settings", lambda: _fake_settings(algorithm))

    assert security._decode_admin_token(_token(), db=None) is None
    assert security._decode_user_token(_token(), db=None) is None
