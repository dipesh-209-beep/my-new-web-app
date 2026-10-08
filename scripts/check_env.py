"""Verify the local .env files exist and hold real secrets.

Used by `make backend-env-check`, which `make prod-check` depends on.
(An earlier draft claimed CI also runs this; the CI jobs build their own
env in-process, so this script is a host/dev preflight only.) Split out of
the Makefile because a recipe runs each line in a separate shell, so a
heredoc'd Python block there cannot work.

Exits non-zero and names every problem at once, rather than failing on the
first one, so a single run tells the operator everything to fix.
"""
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# path -> keys that must be present, non-placeholder, and long enough.
REQUIRED: dict[str, list[str]] = {
    ".env": ["POSTGRES_PASSWORD", "REDIS_PASSWORD"],
    "backend/.env": ["ADMIN_API_KEY", "JWT_SECRET_KEY"],
}

# Matches _looks_like_placeholder_secret in backend/app/core/config.py: the
# process will refuse to start on anything shorter than this, so a check that
# accepted it would be checking something the app does not.
MIN_SECRET_LENGTH = 32

_PLACEHOLDER_PREFIXES = ("change_me", "changeme", "your-", "placeholder")


def main() -> int:
    problems: list[str] = []

    for rel_path, keys in REQUIRED.items():
        path = REPO_ROOT / rel_path
        if not path.exists():
            problems.append(f"{rel_path} is missing (run 'make env')")
            continue

        text = path.read_text()
        for key in keys:
            match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
            if not match:
                problems.append(f"{rel_path}: {key} is not set")
                continue
            value = match.group(1).strip()
            if not value:
                problems.append(f"{rel_path}: {key} is empty")
            elif value.lower().startswith(_PLACEHOLDER_PREFIXES):
                problems.append(f"{rel_path}: {key} still has its placeholder value")
            elif len(value) < MIN_SECRET_LENGTH:
                problems.append(
                    f"{rel_path}: {key} is only {len(value)} chars; "
                    f"{MIN_SECRET_LENGTH}+ required"
                )

        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            problems.append(
                f"{rel_path} is mode {mode:o}; secrets should not be group- or "
                f"world-readable (chmod 600)"
            )

    problems.extend(_database_password_drift())

    if problems:
        print("env check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\ngenerate the missing files with:  make env", file=sys.stderr)
        return 1

    print("env files ok: .env and backend/.env present, real secrets, mode 600")
    return 0


def _database_password_drift() -> list[str]:
    """backend/.env's DATABASE_URL must carry the root .env's password.

    The Compose `db` service is initialised with root .env's
    POSTGRES_PASSWORD, while host `uvicorn` and host `pytest` reach the same
    server through backend/.env's DATABASE_URL. One secret, two files, and
    they have drifted: after a password rotation the stack worked normally
    from inside the compose network while every host-side connection failed
    to authenticate -- an error that reads like a wrong database name or a
    refused connection, not like a stale password.

    Reported rather than assumed because the URL also legitimately holds
    host-specific parts (localhost, the dataset database) that must not be
    rewritten; only the password is compared, and `make env` is what fixes
    it. The values themselves are never printed.
    """
    def read(path: pathlib.Path, key: str) -> str | None:
        if not path.exists():
            return None
        match = re.search(rf"^{re.escape(key)}=(.*)$", path.read_text(), re.MULTILINE)
        return match.group(1).strip() if match else None

    password = read(REPO_ROOT / ".env", "POSTGRES_PASSWORD")
    url = read(REPO_ROOT / "backend" / ".env", "DATABASE_URL")
    if not password or not url or not url.startswith("postgresql://"):
        # Either the files are missing (already reported above) or DATABASE_URL
        # is a deliberate non-standard value; not ours to second-guess.
        return []
    match = re.match(r"postgresql://[^:/?#]+:([^@]*)@", url)
    if not match:
        return []
    if match.group(1) == password:
        return []
    return [
        "backend/.env: the DATABASE_URL password does not match .env's "
        "POSTGRES_PASSWORD, so host `uvicorn` and host `pytest` cannot "
        "authenticate against the Compose database. Run `make env` to sync it."
    ]


if __name__ == "__main__":
    raise SystemExit(main())
