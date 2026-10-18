FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Upgrade the build/install tooling too: the pip and setuptools bundled with
# the base image are old enough to carry their own advisories
# (PYSEC-2026-1795/1796/196/2875/2876/3721 for pip; PYSEC-2026-3447 for
# setuptools, fixed in 83.0.0). Neither is an application dependency: the app
# imports only passlib.context and never imports pkg_resources, so no runtime
# code path needs setuptools. It is upgraded rather than removed so pip and
# any build step keep a working setuptools; note that the 8x series drops the
# legacy pkg_resources shim, which the unused passlib.pwd module imports.
RUN pip install --no-cache-dir --upgrade "pip>=26.2" "setuptools>=83.0.0" \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user. Nothing here needs to be writable at
# runtime: docker-compose.yml mounts the root filesystem read_only, so the
# only writable paths are the tmpfs mounts (/tmp), and a non-root UID means
# a compromised process can neither modify the image nor bind a privileged
# port. Development is the deliberate exception and says so in
# docker-compose.override.yml (`user: "0:0"` plus CAP_DAC_OVERRIDE), because
# the dev bind mount drags in backend/.env, which is mode 0600.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
USER appuser

# Stable server by default (no --reload). Local dev hot-reloads via the
# --reload command added by docker-compose.override.yml (auto-loaded by
# `docker compose` in dev); a Deployment that runs this image directly or
# with the override excluded gets a normal, non-restarting uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
