# DeepThreat — putting the app on the web

The app is a normal Flask app, so hosting it means running a WSGI server in front of
`wsgi.py` and giving the platform two things: an environment variable for the session key, and
a command to start. Everything it reads (Sleeper, and Gemini only for Ask AI) is reached
outbound over HTTPS, so no database, volume or inbound network access is needed.

## What the platform has to run

| | |
| --- | --- |
| Build | `pip install -r requirements.txt` |
| Start | `waitress-serve --host=0.0.0.0 --port=$PORT --threads=8 --channel-timeout=300 wsgi:app` |
| Health check | `/healthz` |
| Python | 3.11 — the version this is developed on (see `render.yaml`) |

`waitress` is the production server: pure Python, so the command in `DEPLOY.md`, the one in
`render.yaml` and the one in the `Dockerfile` are the same command, and it is the one that was
run locally to verify this setup rather than a different server that only exists in production.

`--threads=8` because a single page can read a dozen Sleeper endpoints at once, and
`--channel-timeout=300` because the heavier pages (a trades search, the History book) read every
season the league has played and can take a while on the first request. Waitress's default of
120 seconds is enough for most pages but not for the worst of them.

## Environment variables

| Variable | Needed? | What it is |
| --- | --- | --- |
| `SECRET_KEY` | **Yes** | Signs the session cookie. Generate one per environment (`python -c "import secrets;print(secrets.token_hex(32))"`); `render.yaml` has the platform generate it. Without it the app still runs — it generates one per process — but then every restart forgets every session, so set it. |
| `GEMINI_API_KEY` | No | Ask AI only. Without it the tab is hidden and the rest of the app is unaffected. |
| `PORT` | Set by the platform | The port the server binds. |

Nothing else is configurable, and no secret belongs in this repository: `.gitignore` keeps
`api-key.txt` and `*-token.txt` out, and `.dockerignore` keeps them out of an image too.

## What the app does differently when it is served

- **The session key is a secret.** Run from source it is generated per start; served, it is
  read from `SECRET_KEY`, because a constant in the source is a cookie anyone could forge.
- **It can start with nothing remembered.** A server has no `%APPDATA%` and its filesystem may
  be read-only, so the remembered connection is treated as the convenience it is: failing to
  save it never fails a page. The connection is still remembered per browser in the session.
- **`/healthz`** answers without reading Sleeper, so a platform's health check never waits on a
  page's worth of upstream requests.
- **The preload button stays a local convenience.** It warms this process's in-memory cache; on
  a hosted copy with a cold-started instance it warms that instance only. It is harmless.

## Two things worth knowing about a free instance

- A free instance **sleeps when nothing is asking**, and the first request after that waits for
  it to wake (tens of seconds). The pages are read-heavy, so the first hit on a cold instance is
  the slow one; every hit after it is served from the in-memory cache.
- There is **one worker, deliberately**: the cache, the preload and the Sleeper reads all live in
  this process, and more workers would mean reading the same thing several times over.

## Render, concretely

1. Sign in at <https://render.com> (GitHub sign-in is fine) and, from **New → Blueprint**, pick
   this repository. Render reads `render.yaml`, so the service, the build and start commands and
   the health check all come from the repo.
2. When it asks for the variables it cannot generate: leave `GEMINI_API_KEY` empty unless you
   want Ask AI (you can add it later under **Environment**).
3. Deploy. The first build takes a few minutes; the URL is on the service page.
4. Check `/healthz` answers `{"ok": true}`, then open `/` and connect a Sleeper username.

Any other container platform works the same way with the `Dockerfile` (Fly, Koyeb, Cloud Run,
DigitalOcean App Platform) — the `Dockerfile` runs the same two commands, so what was verified
locally is what runs there.

## Verified locally, not on the host

The setup here was checked by running the exact start command against this checkout and reading
the pages back over HTTP, including with a filesystem that cannot be written to. What has *not*
been checked is the platform's own build (its Python patch version, its free-tier limits, its
cold start), because that only exists once an account is connected to it.
