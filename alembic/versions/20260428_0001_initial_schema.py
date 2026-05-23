"""initial schema: messages, meals, meal_prep, daily_options, daily_choices

Revision ID: 20260428_0001
Revises:
Create Date: 2026-04-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260428_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "meals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meals_slot", "meals", ["slot"], unique=False)

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("whapi_message_id", sa.String(length=255), nullable=True),
        sa.Column("from", sa.String(length=255), nullable=True),
        sa.Column("from_name", sa.String(length=255), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("direction", sa.String(length=32), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("whapi_message_id"),
    )
    op.create_index("ix_messages_message_at", "messages", ["timestamp"], unique=False)

    op.create_table(
        "meal_prep",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("meal_id", sa.Integer(), nullable=False),
        sa.Column("pre_prep", sa.Text(), nullable=True),
        sa.Column("ingredients", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_prep_meal_id", "meal_prep", ["meal_id"], unique=False)

    op.create_table(
        "daily_options",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("meal_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_daily_options_date_slot", "daily_options", ["date", "slot"], unique=False
    )

    op.create_table(
        "daily_choices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("meal_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("date", "slot", name="uq_daily_choices_date_slot"),
    )
    op.create_index(
        "ix_daily_choices_date_slot", "daily_choices", ["date", "slot"], unique=False
    )
    op.create_index("ix_daily_choices_meal_id", "daily_choices", ["meal_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_daily_choices_meal_id", table_name="daily_choices")
    op.drop_index("ix_daily_choices_date_slot", table_name="daily_choices")
    op.drop_table("daily_choices")

    op.drop_index("ix_daily_options_date_slot", table_name="daily_options")
    op.drop_table("daily_options")

    op.drop_index("ix_meal_prep_meal_id", table_name="meal_prep")
    op.drop_table("meal_prep")

    op.drop_index("ix_messages_message_at", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_meals_slot", table_name="meals")
    op.drop_table("meals")
