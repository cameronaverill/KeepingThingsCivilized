# Deployment plan (the website only)

Status: **deployed** (Fly.io, app `think-together`, first real deploy 2026-09-28). Scope, per the owner's own
framing: deploy the forum (Phase A) only. The evaluation pipeline (`evaluation/`, `analysis/`, rater/panel
machinery, `docs/plan.md` §9/§14 Steps 11-17) is not deployed — it never runs against the live database and needs
nothing hosted.

**Live incident, 2026-09-28 (see item 9): moderator replies stopped appearing on the deployed site.** Root cause:
the original two-machine "web"/"worker" split never actually worked — a Fly Volume mounts on one host at a time,
so the worker machine could never attach `/data` and crashed on every start, forever. Fixed by combining web and
worker into one process on one machine (§1, §3 item 9). This section's language below still describes them as "two
long-running processes" because that remains true logically; §3 item 9 is where the "two machines" framing was
corrected to "two processes, one machine."

## 1. What actually needs hosting

Two long-running processes, sharing one SQLite database file on a persistent disk:
- **web** — the Django app (`manage.py runserver` today; a real WSGI server in production).
- **worker** — `manage.py run_moderator`, an infinite poll loop that claims pending `ModerationRun` rows and calls
  the Anthropic API. Already handles SIGINT/SIGTERM cleanly (finishes the current run, then exits).

No Postgres, Redis, Celery, or queue service — the app is deliberately SQLite (WAL mode) plus a DB-row-based claim
queue (`moderation/worker.py`'s `claim_next_run`, one conditional `UPDATE`). That constrains hosting choices more
than it seems: this needs a platform that gives a **persistent disk** (SQLite must survive redeploys and restarts)
and lets **two processes run continuously** (not a request-scoped serverless function) — which rules out classic
serverless (Vercel, plain Lambda) and any platform whose filesystem resets on redeploy.

**Hard constraint: exactly one web instance and one worker instance, always.** SQLite has one writer at a time; the
claim query's atomicity assumes one process reads it at a time per conversation, and nothing in this app expects
multi-instance web serving. Whatever platform is chosen, autoscaling must be explicitly off. This app's whole
design (two people, one conversation) means one small instance is enough traffic-wise forever, not just for launch.

## 2. Recommended platform: Fly.io

Fly.io fits this shape closely: persistent volumes, automatic HTTPS via Let's Encrypt, and a free `<app>.fly.dev`
subdomain (a real custom domain is optional, not required to launch). **Corrected 2026-09-28 (§3 item 9):** a Fly
Volume mounts on exactly one host at a time, so this can't be "multiple named process groups... sharing a volume"
across separate machines, as the platform pitch originally read here — it's one machine, running both processes.
Cost at this scale (1 tiny always-on machine + a small volume, no custom domain):

| Item | Estimate |
|---|---|
| `web` machine (shared-cpu-1x, 1024MB — runs both gunicorn and the worker, see §3 item 9) | ~$8/month |
| Volume (1GB, plenty for SQLite + WAL at this scale) | ~$0.15/month |
| TLS cert, `<app>.fly.dev` subdomain | free |
| **Total** | **~$8-9/month** |

(Previously estimated ~$6-7/month across two separate machines, 512MB + 256MB — that split never actually worked;
see item 9. The combined single machine is sized to the sum of what both processes need, plus headroom, not
trimmed back down to the old total.)

A custom domain adds only its own registration cost (~$10-15/year), no platform fee. `manage.py check --deploy`
already exists and should be run against the production settings before the first real deploy.

**Alternatives considered, and why Fly is preferred here:**
- **A small VPS** (Hetzner CX22, ~€4/month) is slightly cheaper and gives full control, but pushes OS patching,
  firewall setup, and manual TLS renewal onto you — real ongoing maintenance for a 2-person forum. Worth it only if
  you specifically want that control; I can write the systemd units + Caddyfile if you'd rather go this way.
- **Railway** is comparable in price and DX, with volumes and multi-service support, but its usage-based hobby
  pricing is less predictable than Fly's flat per-machine cost for an always-on app.
- **Render** charges per service (web + worker = 2 services) at $7/month each on paid tiers ($14/month total) —
  more expensive here, and its free tier's disk isn't persistent across deploys, which this app needs.

## 3. Required repo changes before deploying

Done (numbered items below are in the order they were made; items 4/7/8 record real-deploy fixes made *after*
deployment started, and item 9 records a fix made after that but not yet re-deployed — see the status line at top):

1. **`gunicorn==26.2.0` and `whitenoise==6.12.0`** added to `requirements.txt` (versions resolved live via `uv`;
   installed into `.venv` and verified — full test suite still green).
2. **Whitenoise wired up** in `config/settings.py`: `WhiteNoiseMiddleware` added right after `SecurityMiddleware`;
   `STATIC_ROOT` and a `STORAGES["staticfiles"]` entry (`CompressedManifestStaticFilesStorage`) added.
   `manage.py collectstatic --noinput` verified working (134 files, 402 post-processed) with the settings this
   plan uses in production (`DJANGO_ENV=production`, a real secret key, an explicit host list).
3. **`Dockerfile`** — Python 3.13 slim, installs `requirements.txt` (not the dev file), runs `collectstatic` at
   build time with throwaway build-only settings values, runs as a non-root user. Same image serves both `web` and
   `worker` (see `fly.toml`).
4. **`fly.toml`** — `web` (gunicorn) and `worker` (`manage.py run_moderator`) process groups from that one image,
   a volume mounted at `/data`, `web` pinned to exactly one always-on machine
   (`min_machines_running = max_machines_running = 1`, no scale-to-zero).
   **Real-deploy fix, 2026-09-28:** migration originally ran as `[deploy].release_command`, which failed with
   `DJANGO_DB_PATH points into a folder that does not exist: /data` on the actual first deploy — Fly's
   release-command runs on a separate, ephemeral machine that is **not guaranteed to have the app's volume
   mounted** (volumes are pinned to one host; the release machine isn't necessarily that host). This is a real Fly
   platform behavior, not a bug in the Dockerfile — local Docker verification (§4 below) would not have caught it
   either, since `docker run -v` always mounts a volume regardless. Fixed by moving the migration into `web`'s own
   startup instead (`web = "sh -c \"python manage.py migrate --noinput && exec gunicorn ...\""`), since `web`'s
   machine does have the volume and is pinned to exactly one instance, so the migration can't race against itself.
   This was verified against the real `flyctl` CLI (Fly account created, first real deploy attempted) — everything
   else in this file (machine pinning, mount syntax, process groups) held up as written.
5. **Backup: Litestream, streaming to Backblaze B2** (chosen 2026-09-28 over Cloudflare R2 — B2 is the
   better-documented Litestream target and has a simpler pure-storage pricing model). A Fly volume lives on one
   host; it is not itself a backup. `litestream.yml` (new) is baked into the image at `/etc/litestream.yml`. The
   `Dockerfile` installs the `litestream` `.deb` (pinned `LITESTREAM_VERSION=0.5.17`, verified against the real
   GitHub release asset and its `amd64` architecture). Rather than a third always-on machine, `fly.toml`'s `worker`
   process is `litestream replicate -config /etc/litestream.yml -exec "python manage.py run_moderator"` — Litestream
   starts/stops with the worker and streams the shared `/data/db.sqlite3` regardless of which process (web or
   worker) wrote to it. Known simplification: if the worker machine is down, replication pauses even though web
   could still be accepting writes — accepted for a two-person forum rather than paying for a dedicated machine.
   To restore onto a fresh volume: `litestream restore -config /etc/litestream.yml /data/db.sqlite3`.
   **Not build-tested end-to-end** — no `docker`/`flyctl` available in this environment to actually run
   `docker build .`; I verified the release asset name/architecture and the `.deb`'s own conffile behavior
   (it ships a default `/etc/litestream.yml` that our `COPY` correctly overwrites, in the right order) by hand, but
   a real build should happen before the first deploy.
6. **Environment variables to set as Fly secrets** (never committed, matching `.env.example`'s existing shape):
   `DJANGO_SECRET_KEY` (generate fresh, don't reuse any dev value), `DJANGO_ALLOWED_HOSTS`,
   `DJANGO_CSRF_TRUSTED_ORIGINS`, `ANTHROPIC_API_KEY`. (`DJANGO_ENV` and `DJANGO_DB_PATH` are already in `fly.toml`'s
   `[env]`, not secret.)
   **Also:** `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST=smtp.gmail.com`,
   `EMAIL_PORT=587`, `EMAIL_USE_TLS=true`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (the Gmail App Password — a
   secret, `fly secrets set` only, never `.env` committed or a build arg), and `ALERT_EMAIL` — this is what makes
   the circuit-breaker alert actually reach you instead of only being logged. Easy to forget since it's not needed
   for local dev; **do this before considering the site actually monitored.**
   **Also:** `LITESTREAM_ACCESS_KEY_ID` and `LITESTREAM_SECRET_ACCESS_KEY` (a B2 application key pair, scoped to
   one bucket) — set once the B2 bucket exists; `LITESTREAM_B2_BUCKET`/`_ENDPOINT`/`_REGION` are non-secret and
   already stubbed as `"CHANGE-ME"` in `fly.toml`'s `[env]`, to be filled in with the real bucket's values at that
   point.
7. **Real-deploy fix, 2026-09-28: login failed with a CSRF error ("Your form expired," `forum/templates/403_csrf.html`)
   on the first successful deploy.** Root cause: Fly terminates TLS at its edge and forwards the app plain HTTP,
   marking the real scheme in `X-Forwarded-Proto`; `config/settings.py` never told Django to trust that header, so
   `request.is_secure()` was always `False` behind Fly, and Django's CSRF `Origin` check compared the browser's
   real `https://...` origin against Django's wrongly-computed `http://...` one. Fixed by adding
   `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` to `config/settings.py`, right after the cookie
   security settings. Safe unconditionally (not just in production): the app is never reachable except through
   Fly's own proxy, which sets this header itself and doesn't forward a client-supplied one through, and no
   dev/test request ever carries this header, so it's a no-op locally. Full test suite reverified green after the
   change (215 security/auth tests targeted first, then the full ~9,900-test suite).
8. **Real-deploy fix, 2026-09-28: `web` OOM-killed mid-login** (`gunicorn... Worker (pid:684) was sent SIGKILL!
   Perhaps out of memory?` in `fly logs`). Root cause: 256MB is tight for 2 full sync gunicorn worker processes
   (each loads the whole Django app independently), and Django's Argon2 password hasher uses ~100MB *per password
   check* by default — login is exactly what pushes it over. Fixed two ways: `web`'s `[[vm]]` memory raised
   `256mb` → `512mb` (see the cost table in §2, now ~$6-7/month total, up from ~$4-5); and the gunicorn command
   changed from `--workers 2 --threads 4` (2 full processes, each with its own full copy of the app in memory) to
   `--workers 1 --worker-class gthread --threads 4` (one process, real concurrency via threads, roughly half the
   baseline memory for the same request-handling capacity a 2-person forum needs).
9. **Live-site incident, 2026-09-28: moderator replies stopped appearing at all.** Symptom, reported by the owner:
   the intervention preview (step 19) still worked — a real, LLM-generated note appeared before posting — but the
   actual moderator reply never showed up in the thread after posting anyway. Diagnosis (via `fly status`/`fly
   machine status`/`fly logs`, since this sandbox has no `flyctl`): `web` was healthy; the `worker` process group
   showed **two** machines, both `stopped`, one flagged `†`. Both had crashed within ~6 seconds of every single
   start (`fly machine status`'s event log: `launch` immediately followed by `stopped`/`update`), repeatedly,
   until each hit Fly's own restart cap (`"machine has reached its max restart count of 10"`, `fly logs`). The
   actual application log line, once found:
   ```
   django.core.exceptions.ImproperlyConfigured: DJANGO_DB_PATH points into a folder that does not exist: /data
   ```
   **Root cause: a Fly Volume mounts read/write on exactly one host at a time.** The original design (item 4
   above) already discovered and documented this exact fact once, for the release-command machine, and fixed it
   there — but didn't generalize it to the `worker` process group, which was still given its own separate,
   independently-pinned machine expecting the *same* `[[mounts]]` volume as `web`. Whichever machine actually holds
   the volume (here, `web`, since it was healthy) works; the other can never attach it, on any redeploy, forever.
   The "two worker machines" were just two separate failed attempts at starting one, not evidence of a scaling
   mistake in the ordinary sense. Litestream (chosen for *backup* replication to B2, item 5) was never going to
   fix this either way — it isn't a tool for sharing one *live, writable* SQLite file across machines (that would
   be something like LiteFS); it was always going to inherit whatever host `run_moderator` ran on.

   **Fix: stop splitting web and worker across two machines.** Nothing about this app's traffic needs two
   machines — it's a two-person-at-a-time forum. `fly.toml` now has one process group (`web`), one `[[vm]]`
   (1024MB, replacing the previous 512MB + 256MB pair — see §2's cost table), running **both** gunicorn and the
   litestream-wrapped worker via a new entrypoint, `scripts/fly_start.sh`, which mirrors `scripts/dev.sh`'s
   existing job-control shape for running two long-lived processes together locally (migrate once, start both,
   forward `SIGTERM`/`SIGINT` to both, exit with whichever process's status if either one dies). `Dockerfile` and
   `scripts/verify_docker_build.sh` (§4) are updated to match — the local verification's steps 4 and 5 are now one
   combined check that the image's one command brings up both the HTTP endpoint and the worker together.

   **Not yet done, on the real Fly account (needs the owner's own `flyctl` access, not run from here):**
   confirm via `fly volumes list -a think-together` whether this ever produced a second, orphaned volume (in
   addition to the two orphaned machines already found) and clean it up if so; destroy the two dead `worker`
   machines; `fly deploy` this fix; confirm a posted message gets a moderator reply again on the real site.

## 4. Local build verification (do before the first real deploy)

None of this needs a Fly account or costs anything — it just needs Docker on your own machine (this sandbox has
neither `docker` nor `flyctl`, so none of it has been run yet; see the caveats in §3 items 4-5 above).

**Run it in one go:** `scripts/verify_docker_build.sh` does all four checks below back to back, fails loudly
(non-zero exit, printing the container's logs) on the first one that doesn't check out, and cleans up its own
image/volume/containers on exit either way. Same checks, spelled out here so you know what it's actually doing:

```bash
# 1. Build the image.
docker build -t think-together .

# 2. It should run manage.py check --deploy cleanly with throwaway secrets in the same shape Fly will use.
docker run --rm \
  -e DJANGO_ENV=production \
  -e DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')" \
  -e DJANGO_ALLOWED_HOSTS=localhost \
  -e DJANGO_DB_PATH=/tmp/check.sqlite3 \
  think-together python manage.py check --deploy

# 3. Confirm Litestream actually installed -- this is the one piece I could not verify from this sandbox.
docker run --rm think-together litestream version

# 4. The real combined command (scripts/fly_start.sh: migrate, then gunicorn AND the litestream-wrapped worker
#    together), using a throwaway volume standing in for Fly's /data mount. This runs the exact fly.toml "web"
#    process group's command, not the Dockerfile's default CMD -- and it's the ONLY process group now (item 9:
#    a Fly Volume mounts on one host at a time, so web and worker can no longer be separate machines).
#    Fake B2 creds are fine here -- this only proves litestream starts and still runs the wrapped worker command,
#    not that it can reach B2 (a real bucket/keys are a separate, later check once you've created them).
docker volume create tt-smoke-data
docker run --rm -p 8080:8080 \
  -e DJANGO_ENV=production -e DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')" \
  -e DJANGO_ALLOWED_HOSTS=localhost -e DJANGO_DB_PATH=/data/db.sqlite3 \
  -e LITESTREAM_B2_BUCKET=test -e LITESTREAM_B2_ENDPOINT=test.example.com -e LITESTREAM_B2_REGION=test \
  -e LITESTREAM_ACCESS_KEY_ID=test -e LITESTREAM_SECRET_ACCESS_KEY=test \
  -v tt-smoke-data:/data \
  think-together bash scripts/fly_start.sh
# In another terminal: curl -I http://localhost:8080/accounts/login/  -> expect "HTTP/1.1 200 OK".
# And in the container's own output: expect the worker's own "Worker started: mode=worker, ..." line alongside
# gunicorn's. Litestream will separately log a connection failure to the fake B2 endpoint -- that's expected and
# fine; it only proves -exec wrapping works.
# Ctrl-C the container when done looking.

# Cleanup.
docker volume rm tt-smoke-data
```

If step 2 fails, it's almost certainly a real settings/Dockerfile bug worth fixing before touching Fly at all.
If step 3 fails, something about the Litestream install (the one piece I couldn't check from here) is wrong and
needs a look before backups can be trusted. If step 4 fails on the worker's line specifically but not on the HTTP
check, don't just retry it — that's exactly the shape of the item 9 incident (one process starts, the other can't
reach `/data`), so re-check `scripts/fly_start.sh` and the volume mount before assuming it's transient.

## 5. Open decisions (yours to make, not mine)

- **Custom domain vs. `<app>.fly.dev`.** The free subdomain is enough to launch on; a custom domain is a purely
  cosmetic/optional upgrade you can add later without redeploying anything else.
- **VPS vs. Fly.io**, if you'd rather trade a bit more setup effort for a slightly lower and more predictable bill,
  or specifically want full server control.

(Email/SMTP and the B2-vs-R2 backup choice were open questions here too, as of this plan's first draft — both are
now decided and wired up; see §3 items 5-6.)

## 6. What I have not done

**Superseded, 2026-09-28: the app is now deployed** (§0 status line, §3 items 4/7/8/9), with your Fly account,
billing, and real deploys already in place — this section's original claim ("nothing has been deployed") no longer
holds and is kept only as history below it. No custom domain/DNS has been set up. The item 9 fix itself (this
document, `fly.toml`, `Dockerfile`, `scripts/fly_start.sh`, `scripts/verify_docker_build.sh`) is drafted here but
**not yet applied to the real Fly app** — actually running `fly deploy`, destroying the two dead `worker` machines,
and checking for an orphaned second volume are all still yours to do (I have no `flyctl`/Fly credentials in this
sandbox). Per standing project rules, I wouldn't take any of those actions on your account even if I could without
your separate, explicit go-ahead.
