"""CHECK constraints: meal slot JSONB and daily slot strings.

Revision ID: 20260430_0003
Revises: 20260429_0002
Create Date: 2026-04-30

"""

from typing import Sequence, Union

from alembic import op

revision: str = "20260430_0003"
down_revision: Union[str, None] = "20260429_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres CHECK must not use subqueries. Use jsonb <@ (contained by): every
    # array element of slot must appear in the allowed master array.
    op.execute(
        """
        ALTER TABLE meals ADD CONSTRAINT ck_meals_slot_allowed_values CHECK (
            jsonb_typeof(slot) = 'array'
            AND slot <@ '["breakfast", "lunch", "dinner"]'::jsonb
        )
        """
    )
    op.execute(
        """
        ALTER TABLE daily_options ADD CONSTRAINT ck_daily_options_slot_allowed CHECK (
            slot IN ('breakfast', 'lunch', 'dinner')
        )
        """
    )
    op.execute(
        """
        ALTER TABLE daily_choices ADD CONSTRAINT ck_daily_choices_slot_allowed CHECK (
            slot IN ('breakfast', 'lunch', 'dinner')
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE daily_choices DROP CONSTRAINT IF EXISTS ck_daily_choices_slot_allowed"
    )
    op.execute(
        "ALTER TABLE daily_options DROP CONSTRAINT IF EXISTS ck_daily_options_slot_allowed"
    )
    op.execute("ALTER TABLE meals DROP CONSTRAINT IF EXISTS ck_meals_slot_allowed_values")
