"""Create the local .env files from their .example templates, with real
generated secrets, on first run.

Creates two files, each only if it does not already exist:

  backend/.env   app secrets   -- ADMIN_API_KEY, JWT_SECRET_KEY
  .env           deploy config -- POSTGRES_PASSWORD, REDIS_PASSWORD

Why two files rather than one: they are consumed by different processes on
different trust boundaries. backend/.env is read by the FastAPI app (via
pydantic-settings, including during `pytest` on the host, where
DATABASE_URL must point at localhost rather than the compose service name).
.env is read by `docker compose` itself, which interpolates it into
service definitions and environment blocks. Merging them would mean the
app's view of DATABASE_URL and compose's view of DATABASE_URL have to be
the same string, which is false the moment you run the app on the host.

Never overwrites an existing file, so local edits survive re-runs. Secrets
are generated with secrets.token_urlsafe: the output alphabet is
[A-Za-z0-9_-], which is URL-safe, and that matters because
POSTGRES_PASSWORD and REDIS_PASSWORD are interpolated into DATABASE_URL and
REDIS_URL by docker-compose. A password containing `@`, `:`, `/` or `#`
would silently corrupt the connection string.

Run via `make backend-env`, or directly: python3 scripts/gen_backend_env.py
"""
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Each target is (destination, template, generated_secrets, copied_values).
#
# "generated" means this script mints a fresh random value and never
# overwrites an existing one. "copied" means the template's literal value is
# used, and the key is added to an existing file if it is missing there.
# The split matters: these are not interchangeable. A single earlier
# version treated every key as generated, which would have written
# PUBLIC_HOST=<43 chars of base64> -- a value that is not a hostname, and
# would be pasted into a TrustedHostMiddleware allow-list as though it
# were one.
TARGETS = [
    (REPO_ROOT / "backend" / ".env", REPO_ROOT / "backend" / ".env.example",
     {"ADMIN_API_KEY": "ADMIN_API_KEY", "JWT_SECRET_KEY": "JWT_SECRET_KEY"},
     {}),
    (REPO_ROOT / ".env", REPO_ROOT / ".env.example",
     {"POSTGRES_PASSWORD": "POSTGRES_PASSWORD", "REDIS_PASSWORD": "REDIS_PASSWORD"},
     {"PUBLIC_HOST": "PUBLIC_HOST", "PUBLIC_CORS_ORIGINS": "PUBLIC_CORS_ORIGINS"}),
]

# Matches `KEY=value` for the specific keys we care about, so we only ever
# rewrite the secret assignments and leave every comment in the template
# intact. A broader pattern would eat comment lines that happen to contain
# an equals sign.
_ASSIGNMENT = "^{key}=(.*)$"


def _require_key(text: str, key: str, path: Path) -> None:
    if not re.search(_ASSIGNMENT.format(key=re.escape(key)), text, re.MULTILINE):
        # A template that no longer contains the key is a bug worth
        # surfacing: the operator would end up with a file that
        # silently keeps the placeholder.
        raise SystemExit(
            f"{path} is missing {key}= -- refusing to write a .env that "
            f"would keep a placeholder value"
        )


def _fill(text: str, secrets_keys: dict[str, str], copied: dict[str, str], template: Path) -> tuple[str, list[str]]:
    generated: list[str] = []
    for key in secrets_keys:
        _require_key(text, key, template)
        pattern = re.compile(_ASSIGNMENT.format(key=re.escape(key)), re.MULTILINE)
        text = pattern.sub(f"{key}={secrets.token_urlsafe(32)}", text)
        generated.append(key)
    for key in copied:
        # Left exactly as the template has it. The template's example.com
        # default is RFC 2606 reserved and cannot resolve, so a laptop is
        # never accidentally deployable, while `make prod-check` still
        # passes -- the one thing an operator must do before deploying is
        # overwrite these with a real hostname and https:// origin.
        _require_key(text, key, template)
    return text, generated


def _sync_missing_keys(destination: Path, template: Path, copied: dict[str, str]) -> list[str]:
    """Add copied keys that an existing .env predates.

    The main loop deliberately never rewrites an existing secrets file --
    that would rotate a working ADMIN_API_KEY out from under a running
    stack. The cost of that restraint is that a key added to the template
    later never reaches the file, so an operator who ran `make env` months
    ago ends up with a .env that is missing keys the current compose file
    interpolates. That is the silent-gap failure this closes: compose
    either errors with `:?` naming the absent variable, or worse, resolves
    it to something unintended.

    Only non-secret keys are synced, and only ever appended when absent.
    """
    if not copied or not template.exists():
        return []
    existing = destination.read_text()
    template_text = template.read_text()
    added: list[str] = []
    additions: list[str] = []
    for key in copied:
        if re.search(_ASSIGNMENT.format(key=re.escape(key)), existing, re.MULTILINE):
            continue
        match = re.search(
            _ASSIGNMENT.format(key=re.escape(key)), template_text, re.MULTILINE
        )
        if not match:
            continue
        additions.append(f"{key}={match.group(1)}")
        added.append(key)
    if not additions:
        return []
    body = existing if existing.endswith("\n") else existing + "\n"
    body += (
        "\n# Added by scripts/gen_backend_env.py because this file predates\n"
        "# them in .env.example. Not a secret -- safe to edit in place.\n"
        + "\n".join(additions)
        + "\n"
    )
    destination.write_text(body)
    destination.chmod(0o600)
    return added


def _has_placeholder(path: Path) -> list[str]:
    """Placeholder secrets left in an already-existing file.

    Not fatal -- a pre-existing backend/.env from an older version of this
    repo will legitimately have none, and an operator may have hand-set a
    short development value. But it is worth saying out loud, because the
    failure mode otherwise shows up much later as a rejected production
    start.
    """
    try:
        text = path.read_text()
    except OSError:
        return []
    stale = []
    for key in ("ADMIN_API_KEY", "JWT_SECRET_KEY", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        match = re.search(_ASSIGNMENT.format(key=re.escape(key)), text, re.MULTILINE)
        if match and (match.group(1).strip().startswith("change_me") or len(match.group(1).strip()) < 32):
            stale.append(key)
    return stale


def _tighten(destination: Path) -> bool:
    """Ensure a secrets file is not group- or world-readable.

    Applied to files this script did not create as well as ones it did:
    an existing backend/.env from an older checkout will have whatever
    mode `git clone` or an editor left it with, and a world-readable
    ADMIN_API_KEY on a shared machine or a CI box is a real exposure. This
    only ever removes bits, and it is idempotent, so running it on
    something you did not create is safe.
    """
    mode = destination.stat().st_mode & 0o777
    if mode & 0o077:
        destination.chmod(0o600)
        print(f"  tightened {destination.relative_to(REPO_ROOT)} from {mode:o} to 600")
        return True
    return False


def _read_assignment(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    match = re.search(_ASSIGNMENT.format(key=re.escape(key)), path.read_text(), re.MULTILINE)
    return match.group(1).strip() if match else None


def sync_dev_database_url() -> bool:
    """Keep backend/.env's DATABASE_URL password equal to the root .env's
    POSTGRES_PASSWORD.

    The database server's password is set by the root .env, because the
    Compose `db` service is initialised from it. backend/.env needs the
    same password for host `uvicorn` and host `pytest` to connect over
    loopback. So one secret is stored in two files, and they did drift:
    the password was rotated in root .env, the stack kept working from
    inside the compose network, and host runs failed to authenticate with
    an error that looks like a wrong database name rather than a stale
    password.

    Only the password is rewritten, and only if it actually differs. The
    host, port and database name are left alone, since those are
    deliberately host-specific (localhost, and the dataset database rather
    than the disposable test one).
    """
    root_env = REPO_ROOT / ".env"
    backend_env = REPO_ROOT / "backend" / ".env"
    password = _read_assignment(root_env, "POSTGRES_PASSWORD")
    url = _read_assignment(backend_env, "DATABASE_URL")
    if not password or not url:
        return False
    # Only touch a URL that has the expected shape; anything else is a
    # deliberate custom configuration (a socket, a proxy, a different
    # user) and not ours to rewrite.
    if not url.startswith("postgresql://"):
        return False
    match = re.match(r"(postgresql://[^:/?#]+:)([^@]*)(@.*)$", url)
    if not match:
        return False
    head, current, tail = match.groups()
    if current == password:
        return False
    if current.startswith("change_me"):
        return False
    backend_env.write_text(
        re.sub(
            # MULTILINE matters: backend/.env begins with comment lines, so
            # without it `^DATABASE_URL` never matches, the substitution is a
            # silent no-op, and this function reports success while changing
            # nothing. It did exactly that until a check caught the drift.
            _ASSIGNMENT.format(key="DATABASE_URL"),
            f"DATABASE_URL={head}{password}{tail}",
            backend_env.read_text(),
            flags=re.MULTILINE,
        )
    )
    backend_env.chmod(0o600)
    print(
        f"  updated backend/.env DATABASE_URL to use the current "
        f"POSTGRES_PASSWORD from .env (host uvicorn/pytest need these to match)"
    )
    return True


def main() -> None:
    wrote_any = False
    for destination, template, secrets_keys, copied in TARGETS:
        if not template.exists():
            raise SystemExit(f"missing template: {template}")

        if destination.exists():
            stale = _has_placeholder(destination)
            if stale:
                print(
                    f"{destination.relative_to(REPO_ROOT)} already exists but still has a "
                    f"placeholder or short value for: {', '.join(stale)}",
                    file=sys.stderr,
                )
            else:
                print(f"{destination.relative_to(REPO_ROOT)} already exists, leaving secrets alone")
            added = _sync_missing_keys(destination, template, copied)
            if added:
                wrote_any = True
                print(
                    f"  added {', '.join(added)} to "
                    f"{destination.relative_to(REPO_ROOT)} (missing from the existing file)"
                )
            _tighten(destination)
            continue

        text, generated = _fill(template.read_text(), secrets_keys, copied, template)
        destination.write_text(text)
        # 0o600: these are secrets, and the repo has no reason for them to
        # be group- or world-readable on a shared machine.
        destination.chmod(0o600)
        wrote_any = True
        print(
            f"created {destination.relative_to(REPO_ROOT)} with generated "
            f"{', '.join(generated)}"
        )

    if sync_dev_database_url():
        wrote_any = True

    if not wrote_any:
        print("nothing to do")
    else:
        print("\nThese files are gitignored. Do not commit them or paste them anywhere.")


if __name__ == "__main__":
    main()
