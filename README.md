# whattowatch

A personal movie and TV recommender. It learns from your watch history, your own and
external ratings, and your watchlists, and recommends titles that are available in Hungary
on the streaming subscriptions you already have. The UI is available in Hungarian and
English. Full specification: `docs/spec.md`; plan: `docs/plan.md`.

## Quick start

```bash
./start.sh
```

This installs Python and frontend dependencies if needed, creates `.env` from
`.env.example` on first run, builds the web UI, applies database migrations and
starts the server at http://127.0.0.1:8000, opening it in your browser. Options:
`--no-open`, `--no-build`, `--port <n>`, `--lan` (or `--host 0.0.0.0`) to reach the app from
other devices on your local network — set profile passwords first, there is no HTTPS yet
(see `docs/multiuser.md`, phase B). API keys can be entered later in
Settings. `start.sh` installs `uv` itself if it is missing (official installer,
no sudo). It needs Node.js 20.19+ for the UI build; on Ubuntu/Debian run it with
`--install-node` to install Node.js 22 from NodeSource (apt's `nodejs` is too old).

## Docker

```bash
docker compose up --build        # then open http://localhost:8000
```

The image (`Dockerfile`) builds the web UI with Node 22, installs the backend with `uv`
(no dev dependencies) and runs as a non-root user (uid 1000). On every start
`docker-entrypoint.sh` applies the migrations (`wtw db upgrade`) and serves on
`0.0.0.0:8000` inside the container (`WTW_PORT` changes the internal port).

- **Data** lives in `./data` on the host, mounted at `/app/data`: the database
  (`whattowatch.db`), uploaded exports (`imports/`), backups (`backups/`) and `.env`.
  Nothing personal is baked into the image. On Linux the directory must be writable by
  uid 1000 (`sudo chown -R 1000 data`).
- **API keys**: enter them on the Settings page; they are written to `data/.env`
  (`WTW_ENV_FILE=/app/data/.env`, created from `.env.example` on first start, mode `0600`).
  `WTW_ENV_FILE` can also be used outside Docker to keep `.env` somewhere else.
- **LM Studio** on the host is reached at `http://host.docker.internal:1234/v1` (set in
  `docker-compose.yml`; edit it there, since the compose value takes precedence over the
  Settings page). LM Studio must listen on all interfaces ("Serve on local network"),
  otherwise the container cannot connect.
- The **Claude Code (CLI)** provider is **not** available in the container (no `claude`
  CLI or login there); use LM Studio, Anthropic or OpenAI.
- **Daily refresh**: `docker compose exec whattowatch uv run wtw refresh run`, or from the
  host crontab:

  ```
  0 6 * * * cd /ABSZOLUT/UT/whattowatch && docker compose exec -T whattowatch uv run wtw refresh run >> /tmp/whattowatch-refresh.log 2>&1
  ```

  Other CLI commands work the same way (`docker compose exec whattowatch uv run wtw ...`).

### File ownership on Linux

The container starts as root only to make the mounted `./data` directory writable,
then drops to an unprivileged user via `gosu`. Files it creates are owned by
`PUID:PGID` (default `1000:1000`). To keep them owned by your own user:

```bash
PUID=$(id -u) PGID=$(id -g) docker compose up --build
```

or put `PUID=...` and `PGID=...` into a `.env` file next to `docker-compose.yml`.
If `./data` already exists and is owned by root (Docker created it on a first failed
start), the entrypoint fixes its ownership automatically.

## Installation

```
uv sync
cp .env.example .env     # fill in the keys you need
uv run wtw db upgrade
uv run wtw serve         # http://127.0.0.1:8000/api/health
```

## Commands

```bash
uv run wtw tmdb providers --type movie      # HU provider list from TMDB
uv run wtw tmdb probe "Interstellar" --type movie --year 2014
uv run wtw tmdb validate                     # report: data/reports/
uv run wtw tmdb link                         # link your TMDB account (approve in the browser)
uv run wtw tmdb resync-ratings               # push all app ratings to the linked account
uv run wtw tmdb unlink

uv run wtw catalog sync                      # TMDB discover for the six subscriptions (HU)
uv run wtw catalog backfill                  # refresh title details: poster, votes, popularity

uv run wtw users list                        # profiles (households: one per person)
uv run wtw users add <name> [--admin]
uv run wtw users rename <name> <new name>
uv run wtw users delete <name> --yes         # deletes all of the profile's personal data

# Personal commands act on the admin profile unless `--user <name>` is given
# (import, recommend, resolve, llm test, tmdb link/unlink/resync-ratings, settings,
# refresh notifications).
uv run wtw import imdb <ratings.csv> --kind ratings     # your IMDb ratings
uv run wtw import imdb <watchlist.csv> --kind watchlist
uv run wtw import netflix <export.zip> --profile <name>  # Netflix personal data export
uv run wtw import prime <export.zip> --profile ADULT  # Prime Video data export (ADULT|CHILD)

uv run wtw resolve list --status ambiguous   # ambiguous title matches
uv run wtw resolve set <id> movie|tv <tmdb_id>
uv run wtw resolve reject <id>
uv run wtw resolve refresh-candidates [--status ambiguous|unresolved]  # re-check open rows (fills posters, overviews, votes)

uv run wtw recommend run --limit 20 --media-type movie   # recommendation batch
uv run wtw recommend extract-preferences --extractor rules|llm [--provider lmstudio]

uv run wtw settings show                     # app settings (no secrets)
uv run wtw settings subscriptions --disable skyshowtime,apple_tv_plus

uv run wtw llm test [--provider anthropic]   # one-line Hungarian test: model, reply, tokens
uv run wtw llm models                        # models offered by LM Studio

uv run wtw refresh run [--pages 10] [--verify-top 200] [--no-sa]   # daily refresh
uv run wtw refresh sa-sync --types new,removed,expiring,upcoming    # Streaming Availability step only
uv run wtw refresh notifications [--all]     # notifications (unread by default)
```

Imports are idempotent: loading the same file twice does not create duplicates. Keep
personal exports under the gitignored `data/` directory. Exports uploaded through the web UI
are stored under `IMPORTS_DIR` (default `data/imports`) as `<source>/<utc>_<filename>` and
imported on a background thread (`POST /api/import/upload`).

Uploaded exports are shared by the household: the Import page lists every profile's uploads
("Household exports") with the export profiles found in them and who imported what, and any
profile can import a stored file into its **own** app profile without uploading it again
(an admin can also import into another profile). Re-uploading the same file reuses the stored
copy; the uploader or an admin can delete it (previous imports stay). Netflix/Prime profile
lists in Settings come from all household uploads. API: `docs/api.md` (import section),
decisions: `docs/multiuser.md` ("Háztartási megosztás").

## Profiles

The app has household profiles (`docs/multiuser.md`). On the first start, create the first
profile in the web UI (it becomes the admin and is signed in) or with `wtw users add <name>`.
A password per profile is optional (argon2id hash; at least 4 characters); an admin can clear
a forgotten one. The browser keeps the choice in a signed, `HttpOnly` cookie (`wtw_session`,
30 days); every `/api` call except health, the profile list, first-profile creation and
`/api/auth/*` needs it. The signing key `SESSION_SECRET` is generated into `.env` on the
first start when missing (changing it signs everyone out). The cookie is marked `Secure`
whenever the request arrived over HTTPS (directly, or `X-Forwarded-Proto: https` from a reverse
proxy on the same machine). Changing a password ends every other session of that profile.

### Accounts mode

For a family spread over several devices, switch the sign-in mode from the household picker to
**accounts**: people register themselves with a name and a password (at least 8 characters)
and sign in with name + password; the profile list is no longer public.

- Enable it with `WTW_AUTH_MODE=accounts` in `.env` (the old `WTW_NETWORK_MODE=true` still
  works as an alias), or as an admin in Settings (stored in the database, wins over `.env`).
  Switching from the UI requires that your own profile has a password.
- **The first registered account becomes the family admin.** The admin can close
  self-registration (Settings, or `wtw users registration closed`) and invite members with a
  temporary password instead; members can change it themselves.
- Existing profiles without a password cannot sign in in accounts mode until the admin sets a
  temporary password for them (Settings → profiles, or `wtw users set-password <name>`).
- Failed sign-ins are throttled per profile (5 failures → 30 s wait) and per client IP
  (20 failures per 10 minutes); registrations are limited to 20 per IP per 10 minutes.
- Anything beyond the home LAN should go through HTTPS with a reverse proxy in front of the
  app, e.g. [Caddy](https://caddyserver.com) with an automatic certificate:

  ```
  example.com {
      reverse_proxy 127.0.0.1:8000
  }
  ```

  The app trusts `X-Forwarded-For` / `X-Forwarded-Proto` only from a loopback proxy. Plain
  HTTP on the LAN works too (the cookie is then not `Secure`).

Locked out of the UI? Recover from the command line on the server:

```bash
uv run wtw users set-password <name>               # prompts twice, hidden
uv run wtw users set-mode household|accounts       # accounts needs an admin with a password
uv run wtw users registration open|closed
```

## Backup and restore

```
uv run wtw backup export [--out PATH]   # consistent snapshot into BACKUPS_DIR (default data/backups)
uv run wtw backup list
uv run wtw backup restore PATH --yes    # safety snapshot first, then swap + migrate to head
```

A backup is a single SQLite file made with the sqlite3 online backup API, so it is safe to
take while the server runs. It does **not** contain `.env` secrets or the uploaded exports in
`IMPORTS_DIR`. Restore accepts backups from older schema revisions (they are migrated to
head). The same operations are available under `/api/backup` (`docs/api.md`).

## Settings (M7)

App-level settings live in the database (the `app_settings` table); `.env` only holds
secrets and infrastructure. From the command line:

```
uv run wtw settings show
uv run wtw settings subscriptions --disable skyshowtime,apple_tv_plus   # turn subscriptions off/on
uv run wtw settings subscriptions --enable apple_tv_plus
```

A disabled provider is left out of candidate generation, recommendations, filters, new
releases and the chat recommender. The Netflix profile (when `wtw import netflix --profile`
is omitted) comes from the app setting first and only then from `NETFLIX_PROFILE_NAME` in
`.env`; the Prime Video profile type (`ADULT`/`CHILD`) from the app setting, default
`ADULT`. Data sources are described in `docs/adatforrasok.md`. The recommender weights can be
overridden from a YAML file given by `RECOMMENDER_CONFIG_PATH` (`docs/recommender.md`).

### Settings in the UI

API keys entered on the web Settings page (`TMDB_API_KEY`,
`STREAMING_AVAILABILITY_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) and
`LM_STUDIO_BASE_URL` are written to the gitignored `.env` file in the repository root (or to `$WTW_ENV_FILE`)
(atomic replace, `0600` permissions, other lines and comments are preserved). The UI never
gets a key back, only whether it is set and its last 4 characters. A new TMDB key is
checked with a `GET /configuration` call before saving. If a key is also set as a process
environment variable, it takes precedence over `.env`. The choice of LLM provider, model,
cloud text sending and fallback is stored in the database (`app_settings`) and takes
precedence over the `LLM_*` values in `.env` (both `wtw llm test` and
`wtw recommend extract --extractor llm` use it).

### TMDB account

Your in-app star ratings can be pushed to your TMDB account. Linking uses TMDB's official
v3 user authentication: the app requests a token, you approve it on themoviedb.org
(Settings page, or `uv run wtw tmdb link`, which prints the approval URL and waits for
Enter), and the app exchanges the approved token for a session. The app never asks for or
stores your TMDB password. Every profile links its own TMDB account. The session id is a
secret and is written only to the gitignored `.env` as `TMDB_SESSION_ID__<profile id>`
(never sent to the browser, never logged; an older single `TMDB_SESSION_ID` is moved to its
owner's key automatically); the account name/id and the on/off switch for rating sync
(default on) live in the database, per profile.
Sync is one-way: the local rating stays the source of truth (stars x 2 -> TMDB's 1-10),
clearing a rating removes it on TMDB, and a TMDB error is shown on the card but never
breaks the local save. Unlink from the Settings page or with `uv run wtw tmdb unlink`
(revokes the session on TMDB and removes the profile's `TMDB_SESSION_ID__<id>` from `.env`); you can also
revoke access on themoviedb.org. API: `docs/api.md` ("TMDB-fiók").

## Daily refresh

`wtw refresh run` does everything in one step: it refreshes the catalog (TMDB discover plus
verification), pulls the Streaming Availability `changes` events (new, removed, expiring and
upcoming titles), detects new seasons and watchlist titles that have become available, and
then creates notifications (`docs/updates.md`). The leaving-notice thresholds are set with
`LEAVING_NOTICE_DAYS` (default `14,7,3`); the same event never produces a repeated
notification. Without `STREAMING_AVAILABILITY_API_KEY` the SA step is skipped
("Streaming Availability: no key"), and the other steps still run. In the web UI,
`POST /api/refresh` runs the same thing.

**Built-in scheduler:** the server itself guarantees a refresh at least every
`AUTO_REFRESH_INTERVAL_DAYS` days (default 7, `0` = off; the Settings page value overrides
it, together with the preferred local hour, default 6). It checks every 15 minutes; a due
run waits for the preferred hour but never more than a day past due. Runs never overlap
with `POST /api/refresh` or `wtw refresh run` (`409 refresh_running`). cron / launchd below
is optional now, for example for daily runs.

**Quota:** the SA free plan has a monthly request limit; one refresh costs roughly
4 types × the number of pages (by default at most 4 × 10). Run daily, that can reach
~1200 requests a month, so if the quota is tight, use `--no-sa` on some days or run
`wtw refresh sa-sync` less often.

Scheduling on macOS with `launchd`
(`~/Library/LaunchAgents/local.whattowatch.refresh.plist`, load it with
`launchctl load ~/Library/LaunchAgents/local.whattowatch.refresh.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.whattowatch.refresh</string>
  <key>WorkingDirectory</key><string>/ABSZOLUT/UT/whattowatch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string><string>uv run wtw refresh run</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/whattowatch-refresh.log</string>
  <key>StandardErrorPath</key><string>/tmp/whattowatch-refresh.log</string>
</dict>
</plist>
```

Or with crontab (`crontab -e`), run from the repository directory:

```
0 6 * * * cd /ABSZOLUT/UT/whattowatch && uv run wtw refresh run >> /tmp/whattowatch-refresh.log 2>&1
```

## LLM providers

The chat recommender and the LLM analysis of free-text feedback share one provider layer
(`src/whattowatch/llm/`, design: `docs/llm.md`). Provider: `LLM_PROVIDER` (`lmstudio` -
default, local; `anthropic`; `openai`; `claude_code`), model: `LLM_MODEL` (if empty: the first loaded model
for LM Studio, `claude-opus-5-5` for Anthropic, and the placeholder `gpt-4o-mini` for
OpenAI, whose availability on your account is not verified). Embeddings:
`LLM_EMBEDDING_MODEL` (OpenAI-compatible servers only).

- A **ChatGPT or Claude subscription is not API access.** The OpenAI and Anthropic APIs
  need separate sign-up, a separate key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) and separate
  usage-based billing. Keys are read only from environment variables, and `/api/settings`
  only reports whether they are set.
- **No automatic provider switching:** a failure of the selected provider is shown as an
  error. Switching to another provider is only possible with `LLM_FALLBACK_ALLOWED=true`.
- **Privacy:** the raw "What did you like / What didn't you like" text is sent to a cloud
  provider only if `LLM_CLOUD_SEND_FEEDBACK_TEXT=true`; otherwise cloud extraction skips the
  feedback (there is no restriction with local LM Studio).
- `ANTHROPIC_SERVER_FALLBACK` (default `true`): the Anthropic API's own in-call model
  fallback (`server-side-fallback` beta): if the requested model declines for policy
  reasons, the server retries within the same call with a substitute Claude model. This is
  an Anthropic-internal mechanism, not the same as the cross-provider
  `LLM_FALLBACK_ALLOWED`.
- **Claude Code (CLI)** (`LLM_PROVIDER=claude_code`): uses the local Claude Code CLI login
  (your Claude subscription) through the official Claude Agent SDK, no API key. It requires
  `claude` on `PATH` and a prior `claude login`. Model: `LLM_MODEL`, else
  `CLAUDE_CODE_MODEL` (`sonnet` | `opus` | `haiku`), else the CLI default. Every call runs
  isolated: in an empty temporary directory, without project files or settings (no
  `CLAUDE.md`), with all built-in tools disabled; in chat only the backend's typed tools are
  exposed (as an in-process MCP server) and the CLI runs the tool loop. It is a cloud
  provider: data goes to Anthropic, so feedback text is only sent when
  `LLM_CLOUD_SEND_FEEDBACK_TEXT=true`.

## Tests and quality checks

```
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

## Frontend

Vue 3 + TypeScript (Vite) under `frontend/`; API contract: `docs/api.md`.

```
cd frontend
npm install
npm run dev      # http://localhost:5173, proxies /api requests to 127.0.0.1:8000
npm run build    # vue-tsc type check + build into frontend/dist
npm run lint     # eslint (vue + typescript-eslint)
```

The backend serves the contents of `frontend/dist` at the root if it exists (other path: `FRONTEND_DIST`).

**Language (HU/EN):** the UI is translated with `vue-i18n`; the strings live in
`frontend/src/locales/hu.json` (source) and `en.json`, and both files have the same key set.
The language is switched with the HU/EN toggle in the header or on the Settings page; the
choice is stored in the `ui_language` field of `PUT /settings/app` and in the browser's
`localStorage` (`ui_language`). On load, the server value wins, then `localStorage`, and
finally the browser language. In the English UI, titles are ordered: English, original,
Hungarian.
