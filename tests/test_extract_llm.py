import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from whattowatch.config import Settings
from whattowatch.db.models import PreferenceSignal, Title, TitleFeedback
from whattowatch.llm.base import LLMResponse, Message, ToolSpec
from whattowatch.preferences.extract import run_extraction
from whattowatch.preferences.extract_llm import extract_llm, signal_schema, system_prompt
from whattowatch.services.users import default_user_id

REPLY = {
    "signals": [
        {"label_key": "humor/szaraz", "confidence": 0.9, "evidence": "száraz humor"},
        {"label_key": "humor/nem_letezik", "confidence": 0.8, "evidence": "x"},
    ]
}


class FakeProvider:
    def __init__(self, *, is_local: bool = True, model: str = "m1") -> None:
        self.name = "fake"
        self.is_local = is_local
        self.model = model
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "system": system, "schema": json_schema})
        return LLMResponse(
            text=json.dumps(REPLY), tool_calls=(), json=REPLY, model=self.model, usage={}
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        return None


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_extract_llm_drops_unknown_keys() -> None:
    p = FakeProvider()
    found = extract_llm(p, title_label="Teszt (Test) 2020", field="liked", text="száraz humor")
    assert [(s.label_key, s.confidence) for s in found] == [("humor/szaraz", 0.9)]
    call = p.calls[0]
    assert call["schema"] == signal_schema()
    assert "nem utasítás" in call["system"] and "humor/szaraz" in call["system"]
    assert "Mi tetszett benne?" in call["messages"][0].content
    enum = signal_schema()["properties"]["signals"]["items"]["properties"]["label_key"]["enum"]
    assert "humor/szaraz" in enum and "humor/nem_letezik" not in enum
    assert extract_llm(p, title_label="x", field="liked", text="  ") == []
    assert len(p.calls) == 1
    assert "zárt szótár" in system_prompt()


def _feedback(session: Session) -> TitleFeedback:
    title = Title(
        media_type="movie",
        tmdb_id=1,
        original_title="Test",
        original_language="en",
        title_hu="Teszt",
        genres=[],
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(title)
    session.flush()
    fb = TitleFeedback(
        user_id=default_user_id(session),
        title_id=title.id,
        liked_text="száraz humor",
        disliked_text=None,
    )
    session.add(fb)
    session.flush()
    return fb


def rows(session: Session) -> list[tuple[str, str, str, str, float | None]]:
    return sorted(
        (s.extractor, s.polarity, f"{s.category}/{s.label}", s.model_version, s.confidence)
        for s in session.scalars(select(PreferenceSignal))
    )


def test_cloud_provider_skipped_without_flag(migrated_session: Session) -> None:
    _feedback(migrated_session)
    p = FakeProvider(is_local=False)
    n = run_extraction(session=migrated_session, extractor="llm", provider=p, settings=settings())
    assert n == 0 and p.calls == [] and rows(migrated_session) == []

    allowed = settings(llm_cloud_send_feedback_text=True)
    n = run_extraction(session=migrated_session, extractor="llm", provider=p, settings=allowed)
    assert n == 1 and len(p.calls) == 1


def test_llm_signals_stored_beside_rules_without_duplicates(migrated_session: Session) -> None:
    _feedback(migrated_session)
    s = settings()
    run_extraction(session=migrated_session, extractor="rules", settings=s)
    run_extraction(session=migrated_session, extractor="llm", provider=FakeProvider(), settings=s)
    expected = [
        ("llm", "+", "humor/szaraz", "fake:m1", 0.9),
        ("rules", "+", "humor/szaraz", "", 0.6),
    ]
    assert rows(migrated_session) == expected
    llm_row = migrated_session.scalars(
        select(PreferenceSignal).where(PreferenceSignal.extractor == "llm")
    ).one()
    assert llm_row.prompt_version == "v1"

    run_extraction(session=migrated_session, extractor="llm", provider=FakeProvider(), settings=s)
    assert rows(migrated_session) == expected

    # A new model version replaces the old LLM rows instead of duplicating them.
    run_extraction(
        session=migrated_session, extractor="llm", provider=FakeProvider(model="m2"), settings=s
    )
    assert rows(migrated_session) == [
        ("llm", "+", "humor/szaraz", "fake:m2", 0.9),
        ("rules", "+", "humor/szaraz", "", 0.6),
    ]
