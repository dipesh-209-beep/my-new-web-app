"""Preflight: would the backend actually start under the production overlay?

`make prod-check` used to check that the compose files parse and that only
80/443 are published, and call that sufficient. It was not: the backend runs
validate_production_settings() at startup and refuses to boot on several
conditions that a YAML parse cannot see -- a wildcard or plain-http CORS
origin, an empty trusted-host list, the legacy shared admin key, a short
JWT secret, a missing shared rate-limit store, or TRUST_PROXY_HEADERS set
without matching uvicorn flags.

Those are exactly the conditions an operator hits on their first
`make up-prod`, at which point the failure is a container that starts and
immediately exits, in a log nobody is watching yet. This script closes that
gap by asking the same code the container will ask, with the same resolved
environment, and printing the same complaints it would print.

The environment is taken from `docker compose config` rather than from
.env files directly, because interpolation happens in compose: variables
expanded into DATABASE_URL, overridden by `environment:` blocks, and typed
by compose itself. Reconstructing that by hand in a shell would be a second
implementation that drifts from the first.

Nothing is written and no container is started; this reads configuration
and calls a pure function. It is safe to run at any time.

Usage:
    python3 scripts/check_prod_env.py            # checks docker-compose.prod.yml
    python3 scripts/check_prod_env.py -f a.yml -f b.yml ...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

# The service whose resolved environment the backend process will see.
SERVICE = "backend"

# Kept out of any output. The resolved environment contains live database
# and Redis credentials, and a preflight's whole job is to print things.
_NOISE = {"DATABASE_URL", "REDIS_URL", "RATE_LIMIT_REDIS_URL",
          "ADMIN_API_KEY", "JWT_SECRET_KEY"}


def resolved_environment(compose_files: list[str]) -> dict[str, str]:
    """The environment the backend container would start with."""
    result = subprocess.run(
        ["docker", "compose", *compose_files, "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(
            f"docker compose config failed:\n{result.stderr.strip()}\n"
            f"(interpolation errors usually mean a required variable in .env "
            f"is missing or empty)"
        )
    config = json.loads(result.stdout)
    try:
        env = config["services"][SERVICE]["environment"]
    except KeyError:
        sys.exit(
            f"compose config has no services.{SERVICE}.environment -- is the "
            f"backend service named differently now?"
        )
    if isinstance(env, list):  # compose emits either a mapping or KEY=VALUE pairs
        env = dict(
            item.split("=", 1) for item in env if "=" in item
        )
    return {str(k): "" if v is None else str(v) for k, v in env.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-f", "--compose-file", action="append", default=[],
        help="compose file, repeatable; defaults to base + prod overlay",
    )
    args = parser.parse_args()
    files = args.compose_file or [
        "-f", "docker-compose.yml", "-f", "docker-compose.prod.yml",
    ]

    env = resolved_environment(files)

    # Load the app's validator against this environment. os.environ is
    # updated in place because get_settings() reads from it, and the app's
    # own backend/.env sits at a lower priority -- so compose's values win,
    # which is the point.
    for key, value in env.items():
        os.environ[key] = value

    from app.core.config import ProductionConfigError, get_settings, validate_production_settings

    settings = get_settings()

    print("    resolved backend environment:")
    for key in sorted(env):
        if key in _NOISE:
            print(f"      {key}=<set, redacted>")
        else:
            print(f"      {key}={env[key]}")

    try:
        validate_production_settings(settings)
    except ProductionConfigError as exc:
        print(file=sys.stderr)
        print(f"    ERROR: the backend would REFUSE TO START.\n{exc}", file=sys.stderr)
        return 1

    print("    validate_production_settings() accepts this environment")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
