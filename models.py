"""SQLAlchemy ORM models — keep in sync with Alembic revisions."""

from __future__ import annotations

from datetime import date, datetime

from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from database import Base

# JSON `meals.slot` arrays and varchar `daily_options.slot` / `daily_choices.slot` must use these only.
ALLOWED_MEAL_SLOTS: frozenset[str] = frozenset({"breakfast", "lunch", "dinner"})

# Reserved ``meals.id`` values for ``daily_choices`` (seeded by migrations 20260520_0008, 20260522_0009).
SKIP_MEAL_ID = 0
COOK_ABSENT_MEAL_ID = -1
COOK_HOLIDAY_MEAL_ID = -2
SYSTEM_MEAL_IDS: frozenset[int] = frozenset(
    {SKIP_MEAL_ID, COOK_ABSENT_MEAL_ID, COOK_HOLIDAY_MEAL_ID}
)
NO_COOK_MEAL_IDS: frozenset[int] = frozenset({COOK_ABSENT_MEAL_ID, COOK_HOLIDAY_MEAL_ID})


def is_system_meal_id(meal_id: int) -> bool:
    return meal_id in SYSTEM_MEAL_IDS


def is_no_cook_meal_id(meal_id: int) -> bool:
    return meal_id in NO_COOK_MEAL_IDS


def assert_valid_meal_slot_values(slots: list[str]) -> None:
    """Raise ValueError if any entry is not exactly one of breakfast | lunch | dinner."""
    if not isinstance(slots, list):
        raise TypeError(f"slots must be a list, got {type(slots).__name__}")
    allowed = ", ".join(sorted(ALLOWED_MEAL_SLOTS))
    for s in slots:
        if s not in ALLOWED_MEAL_SLOTS:
            raise ValueError(
                f"Invalid slot {s!r}; allowed values are: {allowed} (lowercase)."
            )


class Message(Base):
    """WhatsApp / Whapi message row."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_message_at", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    whapi_message_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    # DB column "from" (reserved in SQL); Python-safe name from_wa.
    from_wa: Mapped[str | None] = mapped_column("from", String(255), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_at: Mapped[datetime | None] = mapped_column(
        "timestamp", DateTime(timezone=True), nullable=True
    )
    # e.g. inbound / outbound — app convention.
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'received'"),
    )
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wa_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Outbound failure details (or other status notes); optional for inbound.
    status_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)


class Meal(Base):
    """Named meal; `slot` is JSONB: a JSON array of strings, each in ALLOWED_MEAL_SLOTS."""

    __tablename__ = "meals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # JSON array of strings, e.g. ["breakfast","lunch"]. Same DB column name "slot".
    slot: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    meal_preps: Mapped[list[MealPrep]] = relationship(
        back_populates="meal", cascade="all, delete-orphan"
    )
    daily_choices: Mapped[list[DailyChoice]] = relationship(back_populates="meal")


class MealPrep(Base):
    """Prep notes and ingredients for a meal.

    pre_prep and ingredients are optional: None or blank/whitespace-only strings
    are normalized to None before persist (same rule as seed_meals).
    """

    __tablename__ = "meal_prep"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    meal_id: Mapped[int] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pre_prep: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingredients: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    meal: Mapped[Meal] = relationship(back_populates="meal_preps")

    @validates("pre_prep", "ingredients")
    def _coerce_optional_text(self, key: str, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError(
                f"MealPrep.{key} must be str or None, got {type(value).__name__}"
            )
        stripped = value.strip()
        return stripped if stripped else None


class DailyOption(Base):
    """Candidate meals for a calendar day and slot (meal_ids = JSONB JSON array of ints)."""

    __tablename__ = "daily_options"
    __table_args__ = (Index("ix_daily_options_date_slot", "date", "slot"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    calendar_date: Mapped[date] = mapped_column("date", Date, nullable=False)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    meal_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)


class DailyChoice(Base):
    """Chosen meal for a calendar day and slot (at most one per date+slot)."""

    __tablename__ = "daily_choices"
    __table_args__ = (
        UniqueConstraint("date", "slot", name="uq_daily_choices_date_slot"),
        Index("ix_daily_choices_date_slot", "date", "slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    calendar_date: Mapped[date] = mapped_column("date", Date, nullable=False)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    meal_id: Mapped[int] = mapped_column(
        ForeignKey("meals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    meal: Mapped[Meal] = relationship(back_populates="daily_choices")


class LLMLog(Base):
    """Comprehensive log of every LLM call (prompt, response, usage, latency, errors).

    Written best-effort by ``llm.llm_service._persist_llm_log`` after each
    ``generate_content`` invocation — both happy path and exceptions.
    """

    __tablename__ = "llm_logs"
    __table_args__ = (
        Index("ix_llm_logs_kind_created_at", "kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    # Caller-supplied label, e.g. "intent_classify", "draft_meal_options".
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    # Full prompt text sent to the model (no truncation).
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # Stripped response text; null when the call raised before completion.
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Token counts, finish_reason, model_version, etc. (best-effort extraction).
    response_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Caller-supplied context (e.g. snippet length, slot, options count).
    request_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    # repr() of the exception when the SDK call raised; null on success.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Trivia(Base):
    """Categorised trivia for Van's greeting messages."""

    __tablename__ = "trivia"
    __table_args__ = (Index("ix_trivia_category", "category"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    trivia: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    last_sent_on: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    embedding: Mapped[TriviaEmbedding | None] = relationship(
        back_populates="trivia_row", uselist=False, cascade="all, delete-orphan"
    )


class TriviaEmbedding(Base):
    """Vector embedding for a trivia row (Cohere embed, stored as JSONB)."""

    __tablename__ = "trivia_embeddings"
    __table_args__ = (
        Index("ix_trivia_embeddings_trivia_id", "trivia_id", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    trivia_id: Mapped[int] = mapped_column(
        ForeignKey("trivia.id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    trivia_row: Mapped[Trivia] = relationship(back_populates="embedding")
