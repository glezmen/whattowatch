"""Settings API (docs/api.md M7, M7b): non-secret settings, secrets status (never the
values), subscriptions with logos/profiles, app settings and the LLM choice."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from functools import lru_cache
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.orm import Session

from whattowatch.adapters.tmdb import TmdbClient, TmdbError
from whattowatch.api.deps import (
    CodedHTTPException,
    OptionalTmdbDep,
    RecommenderConfigDep,
    SessionDep,
    SettingsDep,
    no_tmdb_key,
)
from whattowatch.api.schemas import (
    AppSettingsIn,
    AppSettingsOut,
    ClaudeCodeOption,
    CloudLlmOption,
    LlmOptions,
    LlmSettingsIn,
    LlmSettingsOut,
    LlmTestIn,
    LlmTestOut,
    LmStudioOption,
    ProviderProfileOut,
    SecretItem,
    SecretKey,
    SecretsIn,
    SecretsOut,
    SettingsOut,
    SubscriptionItem,
    SubscriptionsIn,
    SubscriptionsOut,
    TmdbAccountIn,
    TmdbAccountInfo,
    TmdbAccountOut,
    TmdbLinkStartIn,
    TmdbLinkStartOut,
    TmdbResyncOut,
)
from whattowatch.config import Settings, env_file_path
from whattowatch.llm import registry as llm_registry
from whattowatch.services import app_settings as app_cfg
from whattowatch.services import env_file, subscriptions, tmdb_account

router = APIRouter(tags=["settings"])
log = logging.getLogger(__name__)

SECRET_KEYS: tuple[SecretKey, ...] = (
    "TMDB_API_KEY",
    "STREAMING_AVAILABILITY_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)
LM_STUDIO_BASE_URL = "LM_STUDIO_BASE_URL"
ANTHROPIC_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"]
LM_STUDIO_TIMEOUT = 3.0
CLAUDE_CODE_MODELS = ["sonnet", "opus", "haiku"]
CLAUDE_VERSION_TIMEOUT = 3.0
LLM_TEST_PROMPT = "Válaszolj egy szóval: működik?"
# A failed or incomplete logo refresh is retried at most this often (seconds).
LOGO_RETRY_SECONDS = 3600.0


def _secret_value(settings: Settings, key: str) -> str | None:
    value = getattr(settings, key.lower(), None)
    return value if isinstance(value, str) and value else None


def _hint(value: str | None) -> str | None:
    """Last 4 characters, only for values long enough not to give most of it away."""
    return value[-4:] if value and len(value) >= 8 else None


@router.get("/settings", response_model=SettingsOut)
def get_settings_view(
    settings: SettingsDep, cfg: RecommenderConfigDep, session: SessionDep
) -> SettingsOut:
    return SettingsOut(
        tmdb_api_key_set=bool(settings.tmdb_api_key),
        netflix_profile_name=app_cfg.effective_netflix_profile(session, settings),
        lm_studio_base_url=settings.lm_studio_base_url,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
        anthropic_api_key_set=bool(settings.anthropic_api_key),
        openai_api_key_set=bool(settings.openai_api_key),
        llm_fallback_allowed=settings.llm_fallback_allowed,
        llm_cloud_send_feedback_text=settings.llm_cloud_send_feedback_text,
        recommender=cfg.model_dump(),
        ui_language=app_cfg.ui_language(session),
        enabled_subscriptions=subscriptions.enabled_keys(session),
        secrets_set={key: _secret_value(settings, key) is not None for key in SECRET_KEYS},
    )


# --- secrets ------------------------------------------------------------------------------


def _secrets(settings: Settings) -> SecretsOut:
    items = []
    for key in SECRET_KEYS:
        value = _secret_value(settings, key)
        items.append(
            SecretItem(
                key=key,
                set=value is not None,
                hint=_hint(value),
                label_key=f"settings.secrets.label.{key}",
            )
        )
    return SecretsOut(items=items, lm_studio_base_url=settings.lm_studio_base_url)


@router.get("/settings/secrets", response_model=SecretsOut)
def get_secrets(settings: SettingsDep) -> SecretsOut:
    return _secrets(settings)


def _check_tmdb_key(key: str) -> None:
    try:
        with TmdbClient(key) as client:
            client.configuration()
    except TmdbError as exc:
        if exc.status in (401, 403):
            raise CodedHTTPException(
                422, "A TMDB nem fogadta el az API-kulcsot.", "invalid_tmdb_key"
            ) from exc
        raise CodedHTTPException(
            502, f"A TMDB-kulcs nem ellenőrizhető (HTTP {exc.status}).", "tmdb_unreachable"
        ) from exc
    except httpx.HTTPError as exc:
        raise CodedHTTPException(
            502, "A TMDB nem érhető el, a kulcs nem ellenőrizhető.", "tmdb_unreachable"
        ) from exc


@router.put("/settings/secrets", response_model=SecretsOut)
def put_secrets(body: SecretsIn, request: Request, settings: SettingsDep) -> SecretsOut:
    """Writes the given keys to `.env` (null/"" deletes). A new TMDB key is checked with
    `GET /configuration` first: rejected -> 422 `invalid_tmdb_key`, nothing is written."""
    updates: dict[str, str | None] = {
        key: (value or "").strip() or None for key, value in body.values.items()
    }
    new_tmdb = updates.get("TMDB_API_KEY")
    if new_tmdb and new_tmdb != settings.tmdb_api_key:
        _check_tmdb_key(new_tmdb)
    fields: dict[str, Any] = {key.lower(): value for key, value in updates.items()}
    if "lm_studio_base_url" in body.model_fields_set:
        url = (body.lm_studio_base_url or "").strip().rstrip("/") or None
        if url is not None and not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=422, detail="Az LM Studio címe http:// vagy https:// kezdetű legyen."
            )
        updates[LM_STUDIO_BASE_URL] = url
        fields["lm_studio_base_url"] = url or Settings.model_fields["lm_studio_base_url"].default
    if updates:
        try:
            env_file.update_env(env_file_path(), updates)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500, detail=f"A .env nem írható ({type(exc).__name__})."
            ) from exc
        shadowed = sorted(k for k in updates if os.environ.get(k))
        if shadowed:
            # Process environment wins over `.env` in pydantic-settings; names only.
            log.warning("environment variables override the saved .env keys: %s", shadowed)
    override: Settings | None = getattr(request.app.state, "settings", None)
    if override is not None:
        request.app.state.settings = override.model_copy(update=fields)
        return _secrets(request.app.state.settings)
    return _secrets(Settings() if updates else settings)


# --- subscriptions ------------------------------------------------------------------------


def _subscriptions(session: Session, settings: Settings) -> SubscriptionsOut:
    return SubscriptionsOut(
        items=[
            SubscriptionItem(
                key=row.provider.key,
                display_name=row.provider.display_name,
                enabled=row.enabled,
                tmdb_provider_ids=list(row.provider.tmdb_provider_ids),
                logo_url=row.logo_url,
                profiles=[
                    ProviderProfileOut(
                        name=str(p["name"]),
                        primary_lang=p.get("primary_lang"),
                        rows=p.get("rows"),
                    )
                    for p in row.profiles
                ],
                selected_profile=row.selected_profile,
                profile_support="export" if row.profile_support == "export" else "none",
            )
            for row in subscriptions.subscription_rows(session, settings)
        ]
    )


def _maybe_refresh_logos(request: Request, session: Session, tmdb: TmdbClient | None) -> None:
    """Fill missing logos from TMDB; throttled, errors only logged (the logo is optional)."""
    if tmdb is None or not subscriptions.missing_logos(session):
        return
    state = request.app.state
    last: float = getattr(state, "logo_refresh_at", 0.0)
    if last and time.monotonic() - last < LOGO_RETRY_SECONDS:
        return
    state.logo_refresh_at = time.monotonic()
    try:
        subscriptions.refresh_provider_logos(session, tmdb)
        session.commit()
    except Exception as exc:  # noqa: BLE001 - optional decoration, never fail the GET
        session.rollback()
        log.info("provider logo refresh failed: %s", type(exc).__name__)


@router.get("/settings/subscriptions", response_model=SubscriptionsOut)
def get_subscriptions(
    request: Request, session: SessionDep, settings: SettingsDep, tmdb: OptionalTmdbDep
) -> SubscriptionsOut:
    _maybe_refresh_logos(request, session, tmdb)
    return _subscriptions(session, settings)


@router.put("/settings/subscriptions", response_model=SubscriptionsOut)
def put_subscriptions(
    body: SubscriptionsIn, session: SessionDep, settings: SettingsDep
) -> SubscriptionsOut:
    """`{items: [...]}`: partial update per key; `{enabled: [...]}`: enable exactly those
    (empty list allowed). Unknown key -> 422, nothing changes."""
    if body.items is None and body.enabled is None:
        raise HTTPException(status_code=422, detail="`items` vagy `enabled` kell.")
    try:
        if body.enabled is not None:
            subscriptions.set_enabled(session, body.enabled)
        if body.items is not None:
            subscriptions.apply_updates(
                session,
                [
                    subscriptions.SubscriptionUpdate(
                        key=item.key,
                        enabled=item.enabled,
                        selected_profile=item.selected_profile
                        if "selected_profile" in item.model_fields_set
                        else subscriptions.UNSET,
                    )
                    for item in body.items
                ],
            )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(
            status_code=422, detail=f"Érvénytelen előfizetés-beállítás: {exc}"
        ) from exc
    session.commit()
    return _subscriptions(session, settings)


def _app_settings(session: Session, settings: Settings) -> AppSettingsOut:
    return AppSettingsOut(
        ui_language=app_cfg.ui_language(session),
        netflix_profile_name=app_cfg.effective_netflix_profile(session, settings),
        netflix_profiles=app_cfg.netflix_profiles(session),
        onboarding_done=app_cfg.onboarding_done(session),
    )


@router.get("/settings/app", response_model=AppSettingsOut)
def get_app_settings(session: SessionDep, settings: SettingsDep) -> AppSettingsOut:
    """`netflix_profile_name`: the app setting, else `NETFLIX_PROFILE_NAME` from `.env`."""
    return _app_settings(session, settings)


@router.put("/settings/app", response_model=AppSettingsOut)
def put_app_settings(
    body: AppSettingsIn, session: SessionDep, settings: SettingsDep
) -> AppSettingsOut:
    given = body.model_fields_set
    if "ui_language" in given and body.ui_language is not None:
        app_cfg.set_ui_language(session, body.ui_language)
    if "netflix_profile_name" in given:
        subscriptions.apply_updates(
            session,
            [
                subscriptions.SubscriptionUpdate(
                    key="netflix", selected_profile=body.netflix_profile_name
                )
            ],
        )
    if "onboarding_done" in given and body.onboarding_done is not None:
        app_cfg.set_onboarding_done(session, body.onboarding_done)
    session.commit()
    return _app_settings(session, settings)


# --- LLM ----------------------------------------------------------------------------------


def _lm_studio_models(base_url: str) -> tuple[bool, list[str]]:
    """`GET {base_url}/models` (a listing, not an inference call); any error -> unavailable."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", timeout=LM_STUDIO_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return False, []
    items = data.get("data", []) if isinstance(data, dict) else []
    return True, [str(m["id"]) for m in items if isinstance(m, dict) and m.get("id")]


@lru_cache(maxsize=4)
def _claude_version(cli: str) -> str | None:
    """`claude --version` (cached per process and path); any failure -> None."""
    try:
        out = subprocess.run(
            [cli, "--version"],
            capture_output=True,
            text=True,
            timeout=CLAUDE_VERSION_TIMEOUT,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _claude_code_option() -> ClaudeCodeOption:
    cli = shutil.which("claude")
    return ClaudeCodeOption(
        available=cli is not None,
        version=_claude_version(cli) if cli else None,
        models=list(CLAUDE_CODE_MODELS),
    )


def _llm_settings(session: Session, settings: Settings) -> LlmSettingsOut:
    config = llm_registry.effective_llm_config(session, settings)
    available, models = _lm_studio_models(settings.lm_studio_base_url)
    provider = config.provider if config.provider in llm_registry.PROVIDER_NAMES else "lmstudio"
    return LlmSettingsOut(
        provider=provider,  # type: ignore[arg-type]
        model=config.model,
        options=LlmOptions(
            lmstudio=LmStudioOption(
                available=available, base_url=settings.lm_studio_base_url, models=models
            ),
            anthropic=CloudLlmOption(
                key_set=bool(settings.anthropic_api_key), models=list(ANTHROPIC_MODELS)
            ),
            openai=CloudLlmOption(key_set=bool(settings.openai_api_key), models=[]),
            claude_code=_claude_code_option(),
        ),
        cloud_send_feedback_text=config.cloud_send_feedback_text,
        fallback_allowed=config.fallback_allowed,
    )


@router.get("/settings/llm", response_model=LlmSettingsOut)
def get_llm_settings(session: SessionDep, settings: SettingsDep) -> LlmSettingsOut:
    return _llm_settings(session, settings)


@router.put("/settings/llm", response_model=LlmSettingsOut)
def put_llm_settings(
    body: LlmSettingsIn, session: SessionDep, settings: SettingsDep
) -> LlmSettingsOut:
    """Stored in `app_settings`; wins over `.env` (`effective_llm_config`)."""
    given = body.model_fields_set
    if "provider" in given and body.provider is not None:
        app_cfg.set_setting(session, llm_registry.LLM_PROVIDER, body.provider)
    if "model" in given:
        app_cfg.set_setting(session, llm_registry.LLM_MODEL, (body.model or "").strip() or None)
    if "cloud_send_feedback_text" in given and body.cloud_send_feedback_text is not None:
        app_cfg.set_setting(
            session, llm_registry.LLM_CLOUD_SEND_FEEDBACK_TEXT, body.cloud_send_feedback_text
        )
    if "fallback_allowed" in given and body.fallback_allowed is not None:
        app_cfg.set_setting(session, llm_registry.LLM_FALLBACK_ALLOWED, body.fallback_allowed)
    session.commit()
    return _llm_settings(session, settings)


@router.post("/settings/llm/test", response_model=LlmTestOut)
def post_llm_test(body: LlmTestIn, session: SessionDep, settings: SettingsDep) -> LlmTestOut:
    """One short prompt with the chosen (or given) provider; only on the user's click.

    Never 500: configuration and provider errors come back as `ok: false, error`."""
    from dataclasses import replace

    from whattowatch.llm.base import LLMError, Message

    config = llm_registry.effective_llm_config(session, settings)
    if body.provider is not None and body.provider != config.provider:
        config = replace(config, provider=body.provider, model=None)
    if "model" in body.model_fields_set:
        config = replace(config, model=(body.model or "").strip() or None)
    try:
        provider = llm_registry.get_provider(settings, config=config)
        resp = provider.complete(
            [Message(role="user", content=LLM_TEST_PROMPT)], max_tokens=20, temperature=0
        )
    except LLMError as exc:
        return LlmTestOut(ok=False, model=config.model, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - reported to the user, never a 500
        log.info("LLM test failed: %s", type(exc).__name__)
        return LlmTestOut(
            ok=False, model=config.model, error=f"Váratlan hiba: {type(exc).__name__}"
        )
    return LlmTestOut(
        ok=True,
        model=resp.model,
        reply=(resp.text or "").strip(),
        usage={k: int(v) for k, v in resp.usage.items()},
    )


# --- TMDB account -------------------------------------------------------------------------


def _tmdb_account_out(st: tmdb_account.AccountStatus) -> TmdbAccountOut:
    info = st.account or {}
    return TmdbAccountOut(
        linked=st.linked,
        account=TmdbAccountInfo(
            id=int(info["id"]), username=info.get("username"), name=info.get("name")
        )
        if st.linked and info.get("id") is not None
        else None,
        rating_sync_enabled=st.rating_sync_enabled,
        pending=st.pending,
        api_key_set=st.api_key_set,
    )


def _set_running_session(request: Request, session_id: str | None) -> None:
    """Mirror the `.env` change into the running app's settings override, if any (without
    one, `update_env` already cleared the `get_settings` cache)."""
    override: Settings | None = getattr(request.app.state, "settings", None)
    if override is not None:
        request.app.state.settings = override.model_copy(update={"tmdb_session_id": session_id})


@router.get("/settings/tmdb-account", response_model=TmdbAccountOut)
def get_tmdb_account(session: SessionDep, settings: SettingsDep) -> TmdbAccountOut:
    """Link status only; the session id is never returned."""
    return _tmdb_account_out(tmdb_account.status(session, settings))


@router.put("/settings/tmdb-account", response_model=TmdbAccountOut)
def put_tmdb_account(
    body: TmdbAccountIn, session: SessionDep, settings: SettingsDep
) -> TmdbAccountOut:
    tmdb_account.set_rating_sync_enabled(session, body.rating_sync_enabled)
    session.commit()
    return _tmdb_account_out(tmdb_account.status(session, settings))


@router.post("/settings/tmdb-account/link/start", response_model=TmdbLinkStartOut)
def post_tmdb_link_start(
    session: SessionDep,
    settings: SettingsDep,
    tmdb: OptionalTmdbDep,
    body: TmdbLinkStartIn | None = None,
) -> TmdbLinkStartOut:
    """New request token (kept as pending); the user approves it at `approval_url`."""
    if tmdb is None:
        raise no_tmdb_key()
    redirect_to = (body.redirect_to or "").strip() or None if body else None
    if redirect_to is not None and not redirect_to.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="A redirect_to http(s):// kezdetű legyen.")
    try:
        started = tmdb_account.start_link(session, settings, tmdb, redirect_to=redirect_to)
    except (TmdbError, httpx.HTTPError) as exc:
        session.rollback()
        raise CodedHTTPException(
            502, "A TMDB nem érhető el, az összekapcsolás nem indítható.", "tmdb_unreachable"
        ) from exc
    session.commit()
    return TmdbLinkStartOut(approval_url=started["approval_url"])


@router.post("/settings/tmdb-account/link/finish", response_model=TmdbAccountOut)
def post_tmdb_link_finish(
    request: Request, session: SessionDep, settings: SettingsDep, tmdb: OptionalTmdbDep
) -> TmdbAccountOut:
    """Approved pending token -> session id in `.env`; not approved/expired -> 422."""
    if tmdb is None:
        raise no_tmdb_key()
    try:
        result = tmdb_account.finish_link(session, settings, tmdb)
    except tmdb_account.TmdbLinkError as exc:
        session.commit()  # an expired pending token is cleared
        raise CodedHTTPException(422, exc.message, "tmdb_link_failed") from exc
    except (OSError, ValueError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=500, detail=f"A .env nem írható ({type(exc).__name__})."
        ) from exc
    session.commit()
    _set_running_session(request, result.session_id)
    return _tmdb_account_out(result.status)


@router.post("/settings/tmdb-account/unlink", response_model=TmdbAccountOut)
def post_tmdb_unlink(
    request: Request, session: SessionDep, settings: SettingsDep, tmdb: OptionalTmdbDep
) -> TmdbAccountOut:
    """Revokes the TMDB session (best effort) and removes it from `.env`."""
    try:
        st = tmdb_account.unlink(session, settings, tmdb)
    except (OSError, ValueError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=500, detail=f"A .env nem írható ({type(exc).__name__})."
        ) from exc
    session.commit()
    _set_running_session(request, None)
    return _tmdb_account_out(st)


@router.post("/settings/tmdb-account/resync", response_model=TmdbResyncOut)
def post_tmdb_resync(
    session: SessionDep, settings: SettingsDep, tmdb: OptionalTmdbDep
) -> TmdbResyncOut:
    """Pushes every local app rating to the linked account (synchronous)."""
    if tmdb is None:
        raise no_tmdb_key()
    if not settings.tmdb_session_id:
        raise CodedHTTPException(409, "Nincs összekapcsolt TMDB-fiók.", "tmdb_not_linked")
    result = tmdb_account.resync_ratings(session, settings, tmdb)
    session.commit()
    return TmdbResyncOut(
        total=result.total, ok=result.ok, error=result.error, skipped=result.skipped
    )
