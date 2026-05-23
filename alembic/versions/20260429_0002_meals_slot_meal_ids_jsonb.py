"""meals.slot and daily_options.meal_ids -> JSONB

Revision ID: 20260429_0002
Revises: 20260428_0001
Create Date: 2026-04-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260429_0002"
down_revision: Union[str, None] = "20260428_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_meals_slot")

    op.execute(
        """
        ALTER TABLE meals
        ALTER COLUMN slot TYPE jsonb
        USING to_jsonb(ARRAY[slot::text])
        """
    )

    op.execute(
        """
        ALTER TABLE daily_options
        ALTER COLUMN meal_ids TYPE jsonb
        USING CASE WHEN meal_ids IS NULL THEN NULL ELSE to_jsonb(meal_ids) END
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE daily_options
        ALTER COLUMN meal_ids TYPE integer[]
        USING CASE
            WHEN meal_ids IS NULL THEN NULL
            ELSE ARRAY(
                SELECT (jsonb_array_elements_text(meal_ids))::integer
            )
        END
        """
    )

    op.execute(
        """
        ALTER TABLE meals
        ALTER COLUMN slot TYPE varchar(64)
        USING (slot->>0)
        """
    )

    op.create_index("ix_meals_slot", "meals", ["slot"], unique=False)
