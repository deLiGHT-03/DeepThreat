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
| `DEEPTHREAT_REMEMBER_FILE` | *(unset)* | Remember the connection in the settings file on a served copy as well, which is only right when that copy has exactly one user — see *Remembering the connection* below. |

Nothing else is configurable, and no secret belongs in this repository: `.gitignore` keeps
`api-key.txt` and `*-token.txt` out, and `.dockerignore` keeps them out of an image too.

## What the app does differently when it is served

- **The session key is a secret.** Run from source it is generated per start; served, it is
  read from `SECRET_KEY`, because a constant in the source is a cookie anyone could forge.
- **It remembers the connection per browser, not per instance.** The settings file belongs to
  the desktop app, where "remembered" and "this one person" are the same thing; served, one file
  is everybody's memory and `/` would open the last visitor's league to whoever arrived next.
  Served, then, it is not read or written and the signed session cookie is the memory, so
  connecting is a one-time step per browser. See *Remembering the connection* just below.
- **`/healthz`** answers without reading Sleeper, so a platform's health check never waits on a
  page's worth of upstream requests. It also names the commit this copy is running, which is how
  a deploy that has fallen behind is visible instead of guessed at; see *Which commit is live*.
- **The preload button stays a local convenience.** It warms this process's in-memory cache; on
  a hosted copy with a cold-started instance it warms that instance only. It is harmless.
- **Its stylesheets and scripts are cached rather than re-asked for, by the browser.** Every
  static URL carries a digest of the file's own bytes and is served with a year of caching, so a
  returning visitor re-downloads nothing and revalidates nothing: a reload of the Matchups page
  on the deployed copy answered all thirteen files from the browser's own cache and asked the
  server for none. What this does **not** buy on a free instance is the edge keeping them --
  Cloudflare answers `cf-cache-status: DYNAMIC` for these URLs, which is its way of saying it
  decided not to store them at all, and Render's own edge caching is documented as being for
  paid web services. So the first visit to a URL still reaches the instance; the header is what
  a paid service, or a Cloudflare zone of your own in front of a custom domain, would need to
  make the second visit free as well.
- **The History page's numbers come from the image, not from the instance.** History is the one
  page that reads a league's whole past — every week of matchups and every week of transactions of
  every season it has played — so what each *finished* season added up to is kept, and a copy
  holding nothing rebuilds the page from that instead of reading those weeks again. A summary is
  only ever used for a season whose every week is scored, and only while the season still agrees
  with what the summary was built from, so a season being played is still read live. The desktop
  app writes its own summaries under `%APPDATA%\DeepThreat\history`; a served copy keeps nothing
  on its host (see *Remembering the connection*), and Render's free instances throw the filesystem
  away when they sleep, so what it rebuilds from is the snapshot committed beside the app in
  `data/history/`. Refresh it with `python tools/build_history_snapshot.py`, which builds the page,
  rebuilds it from the snapshot and refuses to succeed unless the two are byte-identical.
- **It treats its address as public.** Crawlers are asked to stay out and every request is
  counted, against the visitor and against the instance as a whole; see *Keeping crawlers and
  loops out* below for the counts and how to change them.

### Remembering the connection

Where the connection is remembered is the one thing that differs between the desktop app and a
served copy, and it is deliberate rather than incidental: one file on an instance everybody can
reach is not one person's memory, so served it is the session only.

| Where it runs | What remembers the connection |
| --- | --- |
| Your machine (`python run.py`, the installed app) | The settings file under `%APPDATA%\DeepThreat`, and the session beside it. |
| Served (`wsgi.py` on a platform) | The session only. Nothing on the host is read or written, so a visitor gets the connect page until that browser has connected once. |
| Served, with `DEEPTHREAT_REMEMBER_FILE=1` | The settings file as well — for a copy with one user, where everyone arriving *is* that user. |

The two are told apart by what the platform provides: it assigns `PORT` and supplies
`SECRET_KEY`, and the launcher a person runs picks its own port and generates its own key. A
served copy therefore needs no variable set for this — the safe behaviour is the one that needs
no configuration — and the connection it does remember survives a restart, because the platform
supplies the same session key each time.

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
  the slow one; every hit after it is served from the in-memory cache. Sleeping also throws away
  the instance's own filesystem, which is why the History page's season summaries are committed
  to the image rather than written on the host: a file written during one life of the instance is
  not there when it wakes.
- There is **one worker, deliberately**: the cache, the preload and the Sleeper reads all live in
  this process, and more workers would mean reading the same thing several times over.

## Which commit is live

A copy that has not released the newest commit answers the same pages as one that has, so the only
way to tell them apart from the outside is for the running process to say which revision it is.
`/healthz` says it:

```json
{"branch": "master", "commit": "e61a370…", "ok": true, "service": "deepthreat", "source": "render"}
```

`source` is where the answer came from, so a report is never taken for more than it is:

| `source` | The revision came from |
| --- | --- |
| `render` | `RENDER_GIT_COMMIT`, which Render sets for the deploy it built and still serves. |
| `vercel` | `VERCEL_GIT_COMMIT_SHA`, which Vercel sets for the commit a deployment was triggered by. It arrives only once the project has **Enable access to System Environment Variables** turned on (Settings → Environment Variables); a project that has not is read as a host that sets nothing, unless the build stamps one — see “Stamping the revision into a build” below. |
| `host` | `SOURCE_VERSION` or `GIT_COMMIT`, as Heroku and the platforms that copied its convention set it. |
| `environment` | `DEEPTHREAT_COMMIT`, for a host that sets none of its own — a container run with the SHA passed to it. |
| `checkout` | The `git` checkout the process is running out of, which is what a run from source is. `dirty` says whether tracked files have moved on from that commit. |
| `build` | `services/revision.txt`, which a build wrote with `tools/stamp_revision.py` before the copy was packaged — the answer for a host that hands nothing over and leaves no checkout behind. Read after the checkout and not before it. |
| `unknown` | Nothing said. `commit` is `null`, which is a fact about the deploy rather than a failure: a copy that cannot be asked what it is should not pretend to know. |

The branch is read the same way (`DEEPTHREAT_BRANCH`, `RENDER_GIT_BRANCH`,
`VERCEL_GIT_COMMIT_REF`, …), and is `null` when there is none to name — a detached checkout, or
a host that names only the SHA. Nothing is read from Sleeper and nothing is cached in the
platform's sense: the answer is worked out once, when the first health check asks, and cannot
change while the process lives. Compare it against `git rev-parse HEAD` on the branch you pushed.

## Stamping the revision into a build

A host that hands no revision over can still be named. The build runs in the checkout even when
the running copy has nothing left of it, so a build step can write down what it can see, and the
file travels inside the deployment for the app to read:

    python tools/stamp_revision.py            # writes services/revision.txt beside build_info.py
    python tools/stamp_revision.py --strict   # fail the build when it cannot be stamped

The tool answers from the same places `services/build_info.py` does, in the same order — the
host's own variable first, the checkout after it — so whatever a build can work out is what gets
written down, and a host whose variable is withheld but whose build has a `.git` is named anyway.
The stamp is read last of all: a tree git can answer about is the tree itself, dirty flag and all,
and a stamp is what a build recorded when there was no tree to read. It is reported as `build`.

The file is not committed, and `services/revision.txt` is in `.gitignore` for that reason: it
names one build, and a stamp in the repository would have every copy claim a revision nobody
shipped. By default the tool succeeds even when it cannot stamp, because a deploy that happens
without being able to name itself is better than one that does not happen; `--strict` is for a
pipeline that would rather hear about it.On Vercel the hook is committed in the repository, so nothing has to be set in an account only its
owner can open — `vercel.json`, which Vercel reads for a deployment built from that commit:

```json
{ "buildCommand": "python tools/stamp_revision.py" }
```

Three things about that one line, and each is a reason it is one line:

- **The install is a separate step.** Vercel has its own `installCommand` for dependencies and
  detects them from `requirements.txt`; a build command is not where they belong, and naming them
  there would install them twice.
- **It overrides a Build Command set in the dashboard** rather than adding to it — this file
  first, then Project Settings, then the preset's default. So the dashboard field
  (**Settings → Build & Development Settings → Build Command**) does the same job with a click,
  and a copy that has both is following this file.
- **A `[tool.vercel.scripts] build` hook in `pyproject.toml` is not used instead.** It is the
  documented hook for the Flask and FastAPI presets, but a `pyproject.toml` is also a dependency
  manifest, and Vercel has been seen preferring it over `requirements.txt` when both are present
  — which installs no dependencies at all and leaves a deployment that imports nothing. A file the
  app reads is not worth a build that can lose its libraries.

This hook is insurance rather than the reason Vercel can be named. The copy there answered
`source: vercel` as soon as it was running code that read those variables, which is what its
`unknown` had meant all along — the app was not reading them, not the host withholding them — and
the deployment this file was added in built and released normally with it in place. It stays
because it costs one line and covers a project whose system variables are turned off or a host
that never had any; delete it if a stamp nobody needed is worse than the line it takes.

The first deploy after this is the test, and it fails safely: Vercel keeps the last good
deployment when a build fails, so a copy that cannot be built is a deploy that did not happen
rather than a site that is down. If the build or the deployed function is affected, delete this
file and push — the stamp is a nicety, and the app is not. Push the tool with or before this hook,
too: a command naming a file the repository does not have yet fails the build, and the writer's own
tolerance for a build it cannot stamp does not cover not being there at all.

## Asking every copy at once

`tools/check_deployed_revisions.py` asks each host for `/healthz` and compares the revision it
reports with the commit that was pushed, so a deploy that did not land is a line of output rather
than a page somebody has to remember to open and read:

    python tools/check_deployed_revisions.py                 # every known host, against the push
    python tools/check_deployed_revisions.py --wait 600      # keep asking while a deploy lands
    python tools/check_deployed_revisions.py --expect 49eaf8a
    python tools/check_deployed_revisions.py --json revisions.json

The commit it asks for is the pushed one — this branch's upstream, not this HEAD — because work
that has not been pushed is work no host can have, and asking for it would report every host as
behind for a deploy that could not have happened. `--expect` names a commit by hand instead, and
when the two differ the run says so before it asks anything.

Each host comes back as `current`, `behind`, `unknown`, `blocked` or `unreachable`, and only
`current` is quiet. A copy that cannot be asked is reported as unasked and never counted as
current, because a page from a stale copy and a page from a current one are the same bytes; the
exit code is `0` only when every host answered for itself and is current, `1` when a host is
behind, and `2` when a host could not be asked or could not say.

Two of those answers are about a host rather than about a deploy:

- **`blocked` is something answering in the app's place**, which is either a sign-in gate in front
  of the app or a host that is not this app at all. The health check sits behind whatever gates
  the host — the same gate the platform's own `healthCheckPath` is asking — so the app was never
  reached, and nothing can be concluded about the copy behind it. Nothing out here tells the two
  apart, so settle it at the host: a host whose `/login` serves a sign-in page and whose
  `/healthz` has never answered this app's JSON is not a copy of this app, whatever its name.
- **`unknown` is the host not handing its revision over.** The copy answers and says it does not
  know what it is. There are two ways to fix that at the host: expose the platform's own variable
  (the `vercel` row above), or stamp the revision in while it is built. Until one of them is done,
  that copy cannot be told from one that is months behind.

The hosts it asks are the ones in `HOSTS` in the tool — the Vercel copy, which answers as this app
— plus any named with `--host NAME=URL`. **The Render service in `render.yaml` has no URL recorded
in this repository**, so it is passed by hand until one is written down; a hostname guessed from a
service name is how a check ends up reporting on somebody else's app. It asks the network and not
this repository, so it is a command you run after a deploy rather than a check CI can hold a
commit to.

## Checks before a deploy

`render.yaml` sets `autoDeployTrigger: checksPass`, so Render deploys a commit only after the
repository's CI checks pass. The check here is the lineup-slot check
(`.github/workflows/lineup-slots.yml`), which runs `tools/check_lineup_slots.py` on every push and
pull request.

It is deliberately a GitHub Actions job and not a build step. It reads Sleeper over the network
and verifies one league's numbers, so putting it in `buildCommand` would tie every deploy to a
third party's uptime and to that league's data rather than to this repository's code. Instead the
workflow checks a *finished* season that is already committed under `data/history/`, whose figures
are frozen, so the run needs no account, no secret and no live league; point the repository
variable `DEEPTHREAT_CHECK_LEAGUE_ID` at another league to check that one instead. Render's own
`preDeployCommand` would be the other place a pre-deploy command could go, but it is available for
paid services only, and this blueprint is `plan: free`.

If Sleeper is unreachable when the workflow runs, the check fails and the deploy waits rather than
releasing. Deploy manually from the dashboard, or set `autoDeploy: true` in `render.yaml` to go
back to deploying on every commit, if that wait is ever in the way.

## Render, concretely

1. Sign in at <https://render.com> (GitHub sign-in is fine) and, from **New → Blueprint**, pick
   this repository. Render reads `render.yaml`, so the service, the build and start commands and
   the health check all come from the repo.
2. When it asks for the variables it cannot generate: leave `GEMINI_API_KEY` empty unless you
   want Ask AI (you can add it later under **Environment**).
3. Deploy. The first build takes a few minutes; the URL is on the service page.
4. Check `/healthz` answers `{"ok": true}` and names the commit it was built from, then open `/`
   and connect a Sleeper username.

Any other container platform works the same way with the `Dockerfile` (Fly, Koyeb, Cloud Run,
DigitalOcean App Platform) — the `Dockerfile` runs the same two commands, so what was verified
locally is what runs there.

## Verified here, and on the host

The setup was checked by running the exact start command against this checkout and reading
the pages back over HTTP, including with a filesystem that cannot be written to. The limits were
checked the same way with the counts turned down (a page limit of three, a data limit of two, Ask
AI at zero), which showed each refusal, its `Retry-After`, the page a throttled visitor is shown,
the Ask AI refusal arriving in the page as an answer, `robots.txt`, the header on every response,
and the health check still answering while everything else was being refused. The layout checker
(`python tools/check_layout.py`, 99 measurements across every page mode) also runs clean at the
defaults, which is the check that says a person using the app will never meet a limit.

Then the same checks on the deployed copy, which answer the questions a local run cannot:

- `/healthz` answers `{"branch": "master", "commit": …, "ok": true, "service": "deepthreat", "source": "render"}`
  from `waitress` — the commit being the one this deploy was built from, so a copy that is behind
  says so — and the Matchups,
  Season, Trades, History, Player Data and Ask AI pages each answer `200` with their stylesheets
  and their titles — the heavy ones read every season the league has played and still come back
  inside the channel timeout.
- The Ask AI page names `gemini-3.5-flash`, which is how the page reads that the platform's
  `GEMINI_API_KEY` was found — not just that the variable is set. That is the model it asks
  first; a question the model cannot answer because it is busy is asked of
  `gemini-3.1-flash-lite` instead, so the name on the page is the setting rather than a promise
  about which one answered a particular question.
- `robots.txt` and `X-Robots-Tag` are on everything the header has to reach: the HTML, the JSON,
  the CSS, the redirect, a `404`, and a refusal.
- The limits refuse in production, not only here: the thirteenth preload in a minute was answered
  `429` with `Retry-After: 49` and the same answer-shaped JSON, and was allowed again once the
  window rolled.
- `CF-Connecting-IP` is not a claim the app has to take on trust. A request carrying one of its
  own is stopped at the edge with `403` before the app sees it, so the address the limiter counts
  is the edge's and not the caller's. `X-Forwarded-For`, `True-Client-IP` and `X-Real-IP` do pass
  through, and are not read, which is why rotating them buys a caller nothing.
- A visitor with no cookie gets the connect page rather than somebody else's league, and the
  league that is open on the copy comes from that browser's own session.
- A cold History render on the deployed copy rebuilds the finished seasons from the snapshot in
  its image, and asks the process what it did (`/league/<id>/preload` reports `summaries`, counted
  per tier, and a restarted process reports all zeroes): the whole chain came back as the same
  65,954 bytes the local app serves, with 60 upstream reads against 309.
- The pages load a 284 KB lossless `.webp` logo rather than the 840 KB PNG (the PNG stays as the
  icon master, and `packaging/make_web_logo.py` regenerates the web one), it is served as
  `image/webp` and is byte-identical to the file in this repository, all thirteen stylesheets
  and scripts on a page carry a digest and answer `public, max-age=31536000, immutable`, and the
  same file asked for without one still answers `no-cache`.

What is still unmeasured on the host itself: its Python patch version and its cold start after
the free instance sleeps.
