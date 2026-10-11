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
- **It treats its address as public.** Crawlers are asked to stay out and every request is
  counted, against the visitor and against the instance as a whole; see *Keeping crawlers and
  loops out* below for the counts and how to change them.

## Keeping crawlers and loops out

The address is public, so the app answers whoever finds it. Two things stop that being a
problem: crawlers are asked to stay away, and anyone is throttled once they have had more than
their share.

- `robots.txt` asks every crawler not to index anything, and every response carries
  `X-Robots-Tag: noindex, nofollow` as well, because the tag in the HTML cannot reach the JSON,
  the relayed images or the redirects, and a crawler that never parses the HTML never reads the
  tag. Neither is a defence — they are a request, and the limits are the defence.
- Requests are counted in `services/rate_limit.py`, in this process. Every limit is kept twice:
  a *visitor* limit, so one noisy address is slowed without anyone else noticing, and a *total*
  limit for the whole instance, which is the one that still holds when the visitor cannot be
  identified at all.
- A refused request answers `429` with `Retry-After`: an ordinary page saying how long the wait
  is, or — for Ask AI and the JSON reads — the same answer-shaped JSON the app already returns,
  so a refusal reads as an answer rather than as a page that broke.
- `/healthz` and the static files are never counted. The health check has to answer while a
  visitor is being throttled, or the platform would replace an instance for doing its job.

The visitor is taken from `CF-Connecting-IP`, which the Cloudflare edge in front of the platform
sets to the address it saw and replaces if the visitor sent one under that name. `X-Forwarded-For`
is deliberately *not* read: a proxy that appends rather than replaces leaves an invented value in
front of the real one, and an identity that can be made up would turn every visitor limit into a
limit on honest visitors. With no trustworthy header the address the socket came from is used,
so everybody shares the one total — the safe way round to be wrong. Hosting somewhere that
replaces `X-Forwarded-For` with the address it saw the request come from? Set
`DEEPTHREAT_TRUST_FORWARDED_FOR=1`.

| Variable | Default | What it does |
| --- | --- | --- |
| `DEEPTHREAT_PAGE_PER_MINUTE` | `120` | Page renders one visitor may ask for in a minute. |
| `DEEPTHREAT_PAGE_TOTAL_PER_MINUTE` | `400` | Page renders everybody together may ask for in a minute. |
| `DEEPTHREAT_DATA_PER_MINUTE` | `180` | JSON reads one visitor may ask for in a minute — set by the Preload button, which watches its own load every 600ms. |
| `DEEPTHREAT_MEDIA_PER_MINUTE` | `300` | Relayed CDN images one visitor may ask for in a minute. |
| `DEEPTHREAT_PRELOAD_PER_MINUTE` | `12` | Preloads one visitor may start in a minute; each one reads a whole league. |
| `DEEPTHREAT_ASK_PER_HOUR` | `6` | Ask AI questions one visitor may ask in an hour. |
| `DEEPTHREAT_ASK_PER_DAY` | `20` | ... and in a day. |
| `DEEPTHREAT_ASK_TOTAL_PER_HOUR` | `15` | Ask AI questions everybody together may ask in an hour. |
| `DEEPTHREAT_ASK_TOTAL_PER_DAY` | `100` | ... and in a day. This is the one that keeps a free Gemini key's quota whole, because a question the key cannot answer spends it anyway. |
| `DEEPTHREAT_TRUST_FORWARDED_FOR` | *(unset)* | Read the visitor from `X-Forwarded-For` (see above). |

A count of `0` allows nothing at all, and a negative count removes that limit — which is how the
whole thing is switched off if you would rather it were not there. The counts live in the
process, so they start over when the instance does, and the only thing kept is an address and
the times it asked: in memory, for as long as its longest window (a day, for Ask AI), never on
disk. A refusal is logged once a minute per visitor, so the log shows the app is being walked
without becoming a flood of its own.

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
the pages back over HTTP, including with a filesystem that cannot be written to. The limits were
checked the same way with the counts turned down (a page limit of three, a data limit of two, Ask
AI at zero), which showed each refusal, its `Retry-After`, the page a throttled visitor is shown,
the Ask AI refusal arriving in the page as an answer, `robots.txt`, the header on every response,
and the health check still answering while everything else was being refused. The layout checker
(`python tools/check_layout.py`, 99 measurements across every page mode) also runs clean at the
defaults, which is the check that says a person using the app will never meet a limit.

What has *not* been checked is the platform's own build (its Python patch version, its free-tier
limits, its cold start), and one header: that Render really does deliver `CF-Connecting-IP` is
read from Cloudflare's documentation and a captured request, not from Render's own, because that
request only exists once the service is live. If it turns out to be absent, the app falls back to
the socket address — every visitor shares one bucket and the total limits do the work, which is
the safe way round for it to be wrong, and the first `429` in the log would say so.
