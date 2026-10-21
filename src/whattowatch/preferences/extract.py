"""Preference signal extraction from "Mi tetszett / Mi nem tetszett" texts.

Rule-based here; `extractor="llm"` dispatches to `extract_llm.py`.

Polarity is never inferred from the text: it comes from the field the text was written
in (liked_text -> "+", disliked_text -> "-"). Negation only drops a match.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.config import Settings, get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import Polarity, PreferenceSignal, TitleFeedback
from whattowatch.llm.base import LLMError, LLMProvider
from whattowatch.preferences.vocabulary import all_labels, by_key

RULES_EXTRACTOR = "rules"
RULES_CONFIDENCE = 0.6
RULES_WEIGHT = 1.0
LLM_EXTRACTOR = "llm"
LLM_WEIGHT = 1.0

NEGATORS = frozenset({"nem", "nincs", "kevés", "alig", "sem", "se"})
NEGATION_WINDOW = 3
# "nem tetszett a kiszámítható befejezés": the negator belongs to the sentiment verb,
# not to the label, so it must not cancel the match.
_SENTIMENT_VERB = re.compile(r"^(tetsz|szeret|bír|élvez|jön be|jött be|zavar)")
_TOKEN = re.compile(r"\w+(?:-\w+)*")
_CLAUSE_BREAK = re.compile(r"[.,;:!?()\n]|\b(?:de|viszont|azonban|ellenben|és)\b")


@dataclass(frozen=True)
class ExtractedSignal:
    label_key: str
    confidence: float = RULES_CONFIDENCE
    evidence: str = ""


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _negated(text: str, start: int) -> bool:
    clause_start = 0
    for m in _CLAUSE_BREAK.finditer(text, 0, start):
        clause_start = m.end()
    prefix = text[clause_start:start]
    tokens = _TOKEN.findall(prefix)
    window = tokens[-NEGATION_WINDOW:]
    offset = len(tokens) - len(window)
    for i, token in enumerate(window):
        if token not in NEGATORS:
            continue
        following = " ".join(tokens[offset + i + 1 : offset + i + 3])
        if _SENTIMENT_VERB.match(following):
            continue
        return True
    return False


def extract_rules(text: str) -> list[ExtractedSignal]:
    """Find vocabulary stems in `text`; one signal per label, in vocabulary order."""
    norm = normalize(text or "")
    if not norm.strip():
        return []
    matches: list[tuple[int, int, str, str]] = []  # (start, end, label_key, stem)
    for label in all_labels():
        for stem in label.keywords_hu:
            for m in re.finditer(re.escape(stem), norm):
                matches.append((m.start(), m.end(), label.full_key, stem))

    def nested(a: tuple[int, int, str, str]) -> bool:
        # A shorter match inside a longer one ("humor" in "száraz humor") is dropped.
        return any(b[0] <= a[0] and a[1] <= b[1] and (b[1] - b[0]) > (a[1] - a[0]) for b in matches)

    found: dict[str, ExtractedSignal] = {}
    for match in sorted(matches):
        start, _end, key, stem = match
        if key in found or nested(match) or _negated(norm, start):
            continue
        found[key] = ExtractedSignal(label_key=key, evidence=stem)
    order = {label.full_key: i for i, label in enumerate(all_labels())}
    return sorted(found.values(), key=lambda s: order[s.label_key])


def _sync_signals(
    session: Session,
    feedback: TitleFeedback,
    desired: dict[tuple[Polarity, str, str], ExtractedSignal],
    *,
    extractor: str,
    model_version: str,
    prompt_version: str,
    weight: float,
    replace_versions: bool = False,
) -> int:
    """Upsert `desired` signals of one (feedback, extractor, model_version) and delete the
    stale ones; rows of other extractors are untouched. With `replace_versions`, rows of
    the same extractor but another model version are replaced too (no duplicates after a
    model/prompt change). Flushes only."""
    query = select(PreferenceSignal).where(
        PreferenceSignal.feedback_id == feedback.id,
        PreferenceSignal.extractor == extractor,
    )
    if not replace_versions:
        query = query.where(PreferenceSignal.model_version == model_version)
    existing: dict[tuple[Polarity, str, str], PreferenceSignal] = {}
    # Current-version rows first so they are the ones kept.
    for old in sorted(session.scalars(query), key=lambda r: r.model_version != model_version):
        key = (old.polarity, old.category, old.label)
        if key in existing or key not in desired:
            session.delete(old)
        else:
            existing[key] = old
    session.flush()
    for key, sig in desired.items():
        polarity, category, label = key
        row = existing.get(key)
        if row is None:
            row = PreferenceSignal(
                user_id=feedback.user_id,
                feedback_id=feedback.id,
                polarity=polarity,
                category=category,
                label=label,
                extractor=extractor,
                model_version=model_version,
            )
            session.add(row)
        row.title_id = feedback.title_id
        row.model_version = model_version
        row.weight = weight
        row.confidence = sig.confidence
        row.prompt_version = prompt_version
    session.flush()
    return len(desired)


def _sources(feedback: TitleFeedback) -> tuple[tuple[Polarity, str | None], ...]:
    return (("+", feedback.liked_text), ("-", feedback.disliked_text))


def extract_for_feedback(
    session: Session,
    feedback: TitleFeedback,
    *,
    model_version: str = "",
    prompt_version: str = "",
) -> int:
    """Sync rule-extracted signals of one feedback row; returns the number upserted.

    Rule signals that no longer match the (edited/cleared) text are deleted; signals
    from other extractors are left untouched. Flushes, does not commit.
    """
    desired: dict[tuple[Polarity, str, str], ExtractedSignal] = {}
    for polarity, text in _sources(feedback):
        for sig in extract_rules(text or ""):
            vocab = by_key(sig.label_key)
            desired[(polarity, vocab.category, vocab.key)] = sig
    return _sync_signals(
        session,
        feedback,
        desired,
        extractor=RULES_EXTRACTOR,
        model_version=model_version,
        prompt_version=prompt_version,
        weight=RULES_WEIGHT,
    )


@dataclass
class LLMExtractionResult:
    upserted: int = 0
    processed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)  # reason -> feedback rows

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _title_label(feedback: TitleFeedback) -> str:
    title = feedback.title
    parts = [title.title_hu or title.original_title]
    if title.title_hu and title.original_title and title.original_title != title.title_hu:
        parts.append(f"({title.original_title})")
    if title.year:
        parts.append(str(title.year))
    return " ".join(parts)


def extract_llm_for_feedback(
    session: Session, feedback: TitleFeedback, provider: LLMProvider
) -> int:
    """Sync LLM-extracted signals of one feedback row (keyed by provider:model).

    Raises `LLMError` before touching the DB if any field fails, so a failed run never
    deletes the last valid signals.
    """
    from whattowatch.preferences.extract_llm import PROMPT_VERSION, extract_llm

    desired: dict[tuple[Polarity, str, str], ExtractedSignal] = {}
    label = _title_label(feedback)
    for polarity, text in _sources(feedback):
        if not (text or "").strip():
            continue
        field_name: Literal["liked", "disliked"] = "liked" if polarity == "+" else "disliked"
        for sig in extract_llm(provider, title_label=label, field=field_name, text=text or ""):
            vocab = by_key(sig.label_key)
            desired[(polarity, vocab.category, vocab.key)] = sig
    return _sync_signals(
        session,
        feedback,
        desired,
        extractor=LLM_EXTRACTOR,
        model_version=f"{provider.name}:{provider.model}",
        prompt_version=PROMPT_VERSION,
        weight=LLM_WEIGHT,
        replace_versions=True,
    )


def run_llm_extraction(
    session: Session, provider: LLMProvider, settings: Settings
) -> LLMExtractionResult:
    """LLM extraction over every feedback row. Cloud providers only receive the raw text
    when `llm_cloud_send_feedback_text` is set; otherwise every row is skipped."""
    result = LLMExtractionResult()
    rows = list(session.scalars(select(TitleFeedback).order_by(TitleFeedback.id)))
    for fb in rows:
        if not ((fb.liked_text or "").strip() or (fb.disliked_text or "").strip()):
            result.skip("empty")
            continue
        if not provider.is_local and not settings.llm_cloud_send_feedback_text:
            result.skip("cloud_text_not_allowed")
            continue
        try:
            result.upserted += extract_llm_for_feedback(session, fb, provider)
        except LLMError:
            result.skip("llm_error")
            continue
        result.processed += 1
    return result


@contextmanager
def _session(session: Session | None) -> Iterator[tuple[Session, bool]]:
    if session is not None:
        yield session, False
        return
    engine = create_engine_from_settings(get_settings())
    own = session_factory(engine)()
    try:
        yield own, True
    finally:
        own.close()
        engine.dispose()


def run_extraction(
    *,
    session: Session | None = None,
    extractor: str = RULES_EXTRACTOR,
    model_version: str = "",
    prompt_version: str = "",
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
) -> int:
    """Re-run extraction over every TitleFeedback row; returns signals upserted.

    `extractor="llm"` uses `provider` (default: the configured one); its model_version is
    `provider:model` and prompt_version the extractor's own. Commits only when it opened
    its own session.
    """
    if extractor not in (RULES_EXTRACTOR, LLM_EXTRACTOR):
        raise ValueError(f"unsupported extractor: {extractor!r} (rules | llm)")
    settings = settings or get_settings()
    with _session(session) as (db, owned):
        if extractor == LLM_EXTRACTOR:
            from whattowatch.llm.registry import (
                apply_config,
                effective_llm_config,
                get_provider,
            )

            config = effective_llm_config(db, settings)
            if provider is None:
                provider = get_provider(settings, config=config)
            result = run_llm_extraction(db, provider, apply_config(settings, config))
            if owned:
                db.commit()
            skipped = ", ".join(f"{k}: {v}" for k, v in sorted(result.skipped.items()))
            typer.echo(
                f"Preferenciajelzések: {result.processed} visszajelzés feldolgozva, "
                f"{result.upserted} jelzés frissítve (kinyerő: llm, "
                f"{provider.name}:{provider.model})."
                + (f" Kihagyva: {skipped}." if skipped else "")
            )
            if result.skipped.get("cloud_text_not_allowed"):
                typer.echo(
                    "Felhős providernek a nyers vélemény csak "
                    "LLM_CLOUD_SEND_FEEDBACK_TEXT=true esetén küldhető el."
                )
            return result.upserted
        feedback_rows = list(db.scalars(select(TitleFeedback).order_by(TitleFeedback.id)))
        total = sum(
            extract_for_feedback(db, fb, model_version=model_version, prompt_version=prompt_version)
            for fb in feedback_rows
        )
        if owned:
            db.commit()
    typer.echo(
        f"Preferenciajelzések: {len(feedback_rows)} visszajelzés feldolgozva, "
        f"{total} jelzés frissítve (kinyerő: {extractor})."
    )
    return total
