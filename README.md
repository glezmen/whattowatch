# whattowatch

A personal movie and TV recommender. It learns from your watch history, your own and
external ratings, and your watchlists, and recommends titles that are available in Hungary
on the streaming subscriptions you already have. The UI is available in Hungarian and
English. Full specification: `docs/spec.md`; plan: `docs/plan.md`.

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

uv run wtw catalog sync                      # TMDB discover for the six subscriptions (HU)
uv run wtw catalog backfill                  # refresh title details: poster, votes, popularity

uv run wtw import imdb <ratings.csv> --kind ratings     # your IMDb ratings
uv run wtw import imdb <watchlist.csv> --kind watchlist
uv run wtw import netflix <export.zip> --profile <name>  # Netflix personal data export

uv run wtw resolve list --status ambiguous   # ambiguous title matches
uv run wtw resolve set <id> movie|tv <tmdb_id>
uv run wtw resolve reject <id>

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
`.env`. Data sources are described in `docs/adatforrasok.md`. The recommender weights can be
overridden from a YAML file given by `RECOMMENDER_CONFIG_PATH` (`docs/recommender.md`).

### Settings in the UI

API keys entered on the web Settings page (`TMDB_API_KEY`,
`STREAMING_AVAILABILITY_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) and
`LM_STUDIO_BASE_URL` are written to the gitignored `.env` file in the repository root
(atomic replace, `0600` permissions, other lines and comments are preserved). The UI never
gets a key back, only whether it is set and its last 4 characters. A new TMDB key is
checked with a `GET /configuration` call before saving. If a key is also set as a process
environment variable, it takes precedence over `.env`. The choice of LLM provider, model,
cloud text sending and fallback is stored in the database (`app_settings`) and takes
precedence over the `LLM_*` values in `.env` (both `wtw llm test` and
`wtw recommend extract --extractor llm` use it).

## Daily refresh

`wtw refresh run` does everything in one step: it refreshes the catalog (TMDB discover plus
verification), pulls the Streaming Availability `changes` events (new, removed, expiring and
upcoming titles), detects new seasons and watchlist titles that have become available, and
then creates notifications (`docs/updates.md`). The leaving-notice thresholds are set with
`LEAVING_NOTICE_DAYS` (default `14,7,3`); the same event never produces a repeated
notification. Without `STREAMING_AVAILABILITY_API_KEY` the SA step is skipped
("Streaming Availability: no key"), and the other steps still run. In the web UI,
`POST /api/refresh` runs the same thing.

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
default, local; `anthropic`; `openai`), model: `LLM_MODEL` (if empty: the first loaded model
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
