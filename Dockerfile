# Deploy image for Fly.io (docs/deployment_plan.md). fly.toml's one "web" process group runs BOTH gunicorn and the
# litestream-wrapped moderator worker from this image, via scripts/fly_start.sh -- a Fly Volume mounts on only one
# host at a time, so these can no longer be two separate machines (docs/deployment_plan.md item 9, 2026-09-28).
FROM python:3.13-slim

# django-axes/argon2-cffi need a C toolchain to build from source on some platforms; kept minimal. curl is only for
# fetching the litestream .deb below and is removed again in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Litestream: continuously streams the SQLite WAL to Backblaze B2 (docs/deployment_plan.md). Runs wrapped around
# the worker process (see fly.toml); reads/writes the same volume-mounted db file web and worker already share.
ARG LITESTREAM_VERSION=0.5.17
RUN curl -fsSL -o /tmp/litestream.deb \
      "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-x86_64.deb" \
    && dpkg -i /tmp/litestream.deb \
    && rm /tmp/litestream.deb \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY litestream.yml /etc/litestream.yml

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Baked into the image, not the volume: static assets never change per-deploy without a new image anyway. The key
# below is a deliberately public, fake placeholder (never the real key) satisfying settings.py's production checks
# at build time only; collectstatic doesn't touch the database or send mail, so it never reaches the running container.
RUN DJANGO_ENV=production DJANGO_SECRET_KEY="build-time-only-not-the-real-key-0000000000000000000000" DJANGO_ALLOWED_HOSTS=build.invalid python manage.py collectstatic --noinput  # secret-scan: allow

RUN useradd --create-home appuser
USER appuser

# Overridden per process group in fly.toml; this is just a safe default for `docker run` without arguments.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8080"]
