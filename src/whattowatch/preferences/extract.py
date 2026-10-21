"""Rule-based preference signal extraction from "Mi tetszett / Mi nem tetszett" texts.

Polarity is never inferred from the text: it comes from the field the text was written
in (liked_text -> "+", disliked_text -> "-"). Negation only drops a match.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.config import get_settings
from whattowatch.db.engine import create_engine_from_settings, session_factory
from whattowatch.db.models import Polarity, PreferenceSignal, TitleFeedback
from whattowatch.preferences.vocabulary import all_labels, by_key

RULES_EXTRACTOR = "rules"
RULES_CONFIDENCE = 0.6
RULES_WEIGHT = 1.0

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
    sources: tuple[tuple[Polarity, str | None], ...] = (
        ("+", feedback.liked_text),
        ("-", feedback.disliked_text),
    )
    for polarity, text in sources:
        for sig in extract_rules(text or ""):
            vocab = by_key(sig.label_key)
            desired[(polarity, vocab.category, vocab.key)] = sig

    existing = {
        (s.polarity, s.category, s.label): s
        for s in session.scalars(
            select(PreferenceSignal).where(
                PreferenceSignal.feedback_id == feedback.id,
                PreferenceSignal.extractor == RULES_EXTRACTOR,
                PreferenceSignal.model_version == model_version,
            )
        )
    }
    for key, stale in existing.items():
        if key not in desired:
            session.delete(stale)
    for key, sig in desired.items():
        polarity, category, label = key
        row = existing.get(key)
        if row is None:
            row = PreferenceSignal(
                feedback_id=feedback.id,
                polarity=polarity,
                category=category,
                label=label,
                extractor=RULES_EXTRACTOR,
                model_version=model_version,
            )
            session.add(row)
        row.title_id = feedback.title_id
        row.weight = RULES_WEIGHT
        row.confidence = sig.confidence
        row.prompt_version = prompt_version
    session.flush()
    return len(desired)


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
) -> int:
    """Re-run extraction over every TitleFeedback row; returns signals upserted.

    Commits only when it opened its own session.
    """
    if extractor != RULES_EXTRACTOR:
        raise ValueError(f"unsupported extractor: {extractor!r} (only 'rules' in M3)")
    with _session(session) as (db, owned):
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
