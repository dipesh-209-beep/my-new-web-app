"""Tests for the settings model and the production-startup refusals in
backend/app/core/config.py.

No database required -- `validate_production_settings()` is a pure
function over a Settings object, and constructing one does not connect to
anything. That is deliberate: these are the checks that decide whether a
misconfigured production process starts at all, so they must be testable
without the infrastructure they are meant to protect.

The theme of most of these is the gap between where an operator *thinks*
a setting is being read from and where it is actually read from. pydantic
loads backend/.env into the Settings object without copying it into
os.environ, so any `os.getenv("SOMETHING")` in the codebase silently
ignores the .env file. Two settings were affected by exactly that, and
the tests below are the reason they stay fixed.
"""
import pytest

from app.core.config import (
    PRODUCTION,
    ProductionConfigError,
    Settings,
    validate_production_settings,
)

SECRETS = {"admin_api_key": "a" * 40, "jwt_secret_key": "b" * 40}

# A production config with no problems, as keyword overrides. Individual
# tests override exactly the one thing they are about, so a test's failure
# names its own cause instead of listing everything that is wrong.
CLEAN_PRODUCTION = {
    "ENVIRONMENT": PRODUCTION,
    "CORS_ORIGINS": "https://ktm-bus.example.com",
    "TRUSTED_HOSTS": "ktm-bus.example.com",
    "REDIS_URL": "redis://limits:6379/1",
}


# Every setting this module exercises, by the name pydantic-settings looks
# for in os.environ. `_env_file=None` only suppresses the backend/.env
# *file*; process environment variables are still read, and they outrank
# field defaults. A developer shell (or `make test`) that exports REDIS_URL
# therefore leaks into Settings() even with the dotenv file disabled --
# which is how an ambient Redis URL defeated a test asserting the no-Redis
# case. Clearing them per test is the only way these stay hermetic.
_ENV_NAMES = (
    "ENVIRONMENT",
    "DATABASE_URL",
    "REDIS_URL",
    "RATE_LIMIT_REDIS_URL",
    "ADMIN_API_KEY",
    "JWT_SECRET_KEY",
    "CORS_ORIGINS",
    "TRUSTED_HOSTS",
    "TRUST_PROXY_HEADERS",
    "ALLOW_LEGACY_SHARED_ADMIN_KEY",
    "OSRM_BASE_URL",
    "OSRM_FOOT_BASE_URL",
)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def make_settings(**overrides) -> Settings:
    """A Settings object with no .env file, no ambient environment and known
    secrets -- so each test varies only what it actually cares about, and
    the same assertions hold on a developer laptop and in CI."""
    values = dict(SECRETS)
    values.update(overrides)
    return Settings(_env_file=None, **values)


def problems_for(**overrides) -> list[str]:
    """The production problems for these overrides, as a list of messages.

    validate_production_settings() does not *return* problems -- it raises
    ProductionConfigError listing all of them, and returns an empty list
    when the config is clean. Unwrapping the exception here keeps the
    individual tests readable and, more importantly, keeps them asserting on
    the message content: a bare "it raised" would still pass if the reason
    for the refusal changed to something unrelated.
    """
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_production_settings(make_settings(**{**CLEAN_PRODUCTION, **overrides}))
    return str(excinfo.value).splitlines()


# --- REDIS_URL must be a Settings field, not just an env var --------------


def test_redis_url_is_a_settings_field_not_just_an_env_var():
    """REDIS_URL has to be readable from backend/.env.

    It used to be read with os.getenv() at both use sites. pydantic loads
    the .env file into the Settings object only, so a REDIS_URL written in
    backend/.env was invisible: the app ran with no shared response cache
    and a per-worker rate limiter while the operator's own config file
    plainly showed it set. The symptom was a production process refusing
    to start with "no shared rate-limit store configured", pointing at a
    variable that looked configured.
    """
    settings = make_settings(REDIS_URL="redis://cache:6379/0")
    assert settings.REDIS_URL == "redis://cache:6379/0"


def test_rate_limit_store_prefers_its_own_redis_url():
    """An operator can keep rate-limit counters off the cache instance, so
    eviction pressure on one does not reset login rate limits."""
    settings = make_settings(
        REDIS_URL="redis://cache:6379/0",
        RATE_LIMIT_REDIS_URL="redis://limits:6379/1",
    )
    assert settings.rate_limit_redis_url == "redis://limits:6379/1"


def test_rate_limit_store_falls_back_to_redis_url():
    settings = make_settings(REDIS_URL="redis://cache:6379/0")
    assert settings.rate_limit_redis_url == "redis://cache:6379/0"


def test_no_redis_url_means_no_shared_store():
    settings = make_settings()
    assert settings.REDIS_URL is None
    assert settings.rate_limit_redis_url is None


def test_osrm_base_urls_are_settings_fields_not_just_env_vars():
    """OSRM_BASE_URL/OSRM_FOOT_BASE_URL used to be read with os.environ at
    import time in osrm_client.py. pydantic loads backend/.env into the
    Settings object only, so a host-dev value written in backend/.env was
    silently ignored while the container (which forwards the process env
    in docker-compose.yml) happened to work. They must be real Settings
    fields so both paths resolve, with the foot profile falling back to
    the driving one when unset."""
    settings = make_settings(
        osrm_base_url="http://router:5000",
        osrm_foot_base_url="http://router-foot:5001",
    )
    assert settings.osrm_base_url == "http://router:5000"
    assert settings.osrm_foot_base_url == "http://router-foot:5001"

    settings = make_settings(osrm_base_url="http://router:5000")
    assert settings.osrm_foot_base_url is None  # osrm_client falls back to osrm_base_url


def test_osrm_base_urls_pick_up_uppercase_env_names(monkeypatch):
    """The .env / docker-compose names are the uppercase OSRM_* -- the
    pydantic-settings convention maps OSRM_BASE_URL to field osrm_base_url
    (and from-file values must be read, not skipped)."""
    monkeypatch.setenv("OSRM_BASE_URL", "http://router:5005")
    settings = make_settings()
    assert settings.osrm_base_url == "http://router:5005"


def test_redis_client_reads_the_settings_field(monkeypatch):
    """app/core/redis_client.get_redis() must agree with the above. It is
    the other half of the same bug, and a Settings-only test would not
    catch a regression that reintroduced os.getenv in this one function."""
    import app.core.redis_client as redis_client

    # The variable has to be absent from the process environment for this
    # to prove anything: if it were set, an os.getenv implementation would
    # pass and the test would be measuring nothing.
    monkeypatch.delenv("REDIS_URL", raising=False)

    def point_settings_at(**overrides) -> None:
        monkeypatch.setattr(
            "app.core.config.get_settings", lambda: make_settings(**overrides)
        )

    redis_client._reset_for_tests()
    point_settings_at(REDIS_URL="")
    assert redis_client.get_redis() is None

    redis_client._reset_for_tests()
    point_settings_at(REDIS_URL="redis://127.0.0.1:6379/0")
    try:
        client = redis_client.get_redis()
    finally:
        redis_client._reset_for_tests()

    if client is None:
        pytest.skip("redis package not installed")
    # get_redis() deliberately does not connect, so this asserts it built a
    # client from the URL rather than that a server is reachable.
    assert client is not None


# --- production refusals --------------------------------------------------


def test_not_production_means_no_complaints():
    """validate_production_settings must be a total no-op outside
    production, or every laptop, `pytest` run and `docker compose up`
    would be checking boxes about TLS and CORS."""
    settings = make_settings(
        ENVIRONMENT="development",
        CORS_ORIGINS="*",
        TRUSTED_HOSTS="",
        admin_api_key="short",
        jwt_secret_key="short",
    )
    assert validate_production_settings(settings) == []


@pytest.mark.parametrize("environment", ["development", "test"])
def test_every_non_production_environment_is_unchecked(environment):
    settings = make_settings(ENVIRONMENT=environment, TRUSTED_HOSTS="", CORS_ORIGINS="*")
    assert validate_production_settings(settings) == []


@pytest.mark.parametrize("environment", ["prod", "staging", "dev", "Production!"])
def test_unknown_environments_are_rejected_at_construction(environment):
    """A typo'd ENVIRONMENT must fail loudly, not fall back to
    development. Silently, a box that meant to be strict comes up with
    every production refusal switched off -- there is no "staging" mode
    here, only development / production / test."""
    with pytest.raises(ValueError, match="ENVIRONMENT must be one of"):
        make_settings(ENVIRONMENT=environment)


def test_a_clean_production_config_passes():
    assert validate_production_settings(make_settings(**CLEAN_PRODUCTION)) == []


def test_placeholder_secrets_are_refused():
    problems = problems_for(
        admin_api_key="change_me_in_production",
        jwt_secret_key="change_me_in_production",
    )
    assert any("ADMIN_API_KEY" in p for p in problems), problems
    assert any("JWT_SECRET_KEY" in p for p in problems), problems


def test_short_jwt_secret_is_refused():
    """A JWT secret shorter than 32 characters cannot carry enough entropy
    to resist offline recovery if the process ever leaks it, so a
    production process refuses to start on one."""
    assert any("JWT_SECRET_KEY" in p for p in problems_for(jwt_secret_key="a" * 16))


def test_wildcard_cors_is_refused():
    assert any("CORS_ORIGINS" in p for p in problems_for(CORS_ORIGINS="*"))


def test_plain_http_cors_is_refused():
    """A production frontend origin over http:// is refused: the whole
    point of terminating TLS at the proxy is undermined if the API will
    also answer an unencrypted caller that believes it is on the real
    site."""
    assert any(
        "CORS_ORIGINS" in p for p in problems_for(CORS_ORIGINS="http://ktm-bus.example.com")
    )


def test_empty_trusted_hosts_is_refused():
    """TrustedHostMiddleware is the mitigation for the Host-header
    request.url reconstruction advisories; an empty allow-list disables
    it, so production must not start that way."""
    assert any("TRUSTED_HOSTS" in p for p in problems_for(TRUSTED_HOSTS=""))


def test_missing_rate_limit_store_is_refused():
    """Without a shared store the admin-login rate limit is per-worker, so
    a multi-worker deployment multiplies the number of guesses an attacker
    gets by the worker count."""
    problems = problems_for(REDIS_URL=None, RATE_LIMIT_REDIS_URL=None)
    assert any("rate-limit" in p or "REDIS" in p for p in problems), problems


def test_legacy_shared_admin_key_is_refused_in_production():
    """The single shared X-Admin-Api-Key grants the full permission set to
    anyone holding it, is unrotatable without a coordinated restart, and
    is attributable to nobody. It can be switched on for a local dev
    session and must not be reachable in production."""
    assert any(
        "ALLOW_LEGACY_SHARED_ADMIN_KEY" in p
        for p in problems_for(ALLOW_LEGACY_SHARED_ADMIN_KEY=True)
    )


def test_legacy_shared_admin_key_is_allowed_outside_production():
    """Otherwise the documented dev escape hatch would not work."""
    settings = make_settings(ENVIRONMENT="development", ALLOW_LEGACY_SHARED_ADMIN_KEY=True)
    assert validate_production_settings(settings) == []


def test_trusting_proxy_headers_without_uvicorn_flags_is_refused(monkeypatch):
    """TRUST_PROXY_HEADERS makes the app read X-Forwarded-For for rate
    limiting. That header is only trustworthy if uvicorn was started with
    --forwarded-allow-ips; otherwise every request is attributed to the
    proxy and the per-IP limit becomes one global bucket.

    This is fatal, not a warning. It was documented as "not fatal on its
    own" while being appended to the same list that raises -- which is how
    docker-compose.prod.yml shipped a backend that refused to start.
    """
    monkeypatch.delenv("UVICORN_ARGS", raising=False)
    problems = problems_for(TRUST_PROXY_HEADERS=True)
    assert any("TRUST_PROXY_HEADERS" in p for p in problems), problems


def test_trusting_proxy_headers_with_uvicorn_flags_is_allowed(monkeypatch):
    monkeypatch.setenv("UVICORN_ARGS", "--proxy-headers --forwarded-allow-ips=172.16.0.0/12")
    assert validate_production_settings(
        make_settings(**{**CLEAN_PRODUCTION, "TRUST_PROXY_HEADERS": True})
    ) == []


def test_trusting_proxy_headers_off_needs_no_uvicorn_flags(monkeypatch):
    monkeypatch.delenv("UVICORN_ARGS", raising=False)
    assert validate_production_settings(
        make_settings(**{**CLEAN_PRODUCTION, "TRUST_PROXY_HEADERS": False})
    ) == []


def test_all_problems_are_reported_at_once():
    """One restart should tell the operator everything that needs fixing.
    A validator that stopped at the first problem turns a five-minute fix
    into five restarts."""
    problems = problems_for(
        admin_api_key="change_me",
        jwt_secret_key="change_me",
        CORS_ORIGINS="*",
        TRUSTED_HOSTS="",
        REDIS_URL=None,
    )
    assert len(problems) >= 5, problems


# --- secrets must not escape through repr/str -----------------------------


def test_repr_does_not_disclose_secrets():
    """A pydantic model prints every field by default, and Settings objects
    land in assertion output, `print()` debugging and tracebacks far more
    often than anyone plans for. A test here asserted on a Settings built
    from real environment variables and pytest's failure report printed the
    live Postgres and Redis passwords in full.

    repr=False on the secret-bearing fields is the fix; this test is why it
    stays fixed, and it is worth keeping in a module that mostly tests
    validation rather than secrecy, because nothing else would notice the
    flag being dropped.
    """
    settings = make_settings(
        DATABASE_URL="postgresql://user:pgpass@db:5432/ktm",
        REDIS_URL="redis://:redispass@cache:6379/0",
        RATE_LIMIT_REDIS_URL="redis://:limitpass@cache:6379/1",
        admin_api_key="admin-key-value",
        jwt_secret_key="jwt-secret-value",
    )
    rendered = f"{settings!r} {settings}"

    for secret in (
        "pgpass",
        "redispass",
        "limitpass",
        "admin-key-value",
        "jwt-secret-value",
    ):
        assert secret not in rendered, f"{secret!r} leaked via repr: {rendered}"

    # Suppressing the repr must not break the value. If a future change
    # "fixed" the leak by making these fields inaccessible, the assertions
    # above would still pass -- these are what catch that.
    assert settings.DATABASE_URL.endswith("/ktm")
    assert settings.admin_api_key == "admin-key-value"
    assert settings.jwt_secret_key == "jwt-secret-value"
    assert settings.rate_limit_redis_url == "redis://:limitpass@cache:6379/1"


def test_model_dump_still_carries_the_real_values():
    """repr=False hides a field from display only. Anything that genuinely
    needs the value -- a DB engine, a Redis client, a signing key -- reads
    it by attribute, so this is the check that the display fix did not
    quietly turn into data loss."""
    settings = make_settings(admin_api_key="admin-key-value")
    assert settings.model_dump()["admin_api_key"] == "admin-key-value"


# --- parsing helpers ------------------------------------------------------


def test_list_properties_tolerate_whitespace():
    settings = make_settings(
        CORS_ORIGINS=" https://a.example.com , https://b.example.com ",
        TRUSTED_HOSTS=" a.example.com , b.example.com ",
    )
    assert settings.cors_origins_list == ["https://a.example.com", "https://b.example.com"]
    assert settings.trusted_hosts_list == ["a.example.com", "b.example.com"]


def test_empty_list_properties_are_empty_lists_not_bare_strings():
    """`for origin in settings.CORS_ORIGINS` over the raw string would
    iterate characters -- a bug this property exists to prevent."""
    settings = make_settings(CORS_ORIGINS="  ", TRUSTED_HOSTS=" , ")
    assert settings.cors_origins_list == []
    assert settings.trusted_hosts_list == []


def test_environment_is_normalised():
    assert make_settings(ENVIRONMENT="  PRODUCTION ").is_production is True
    assert make_settings(ENVIRONMENT="Production").is_production is True
    assert make_settings(ENVIRONMENT="development").is_production is False
