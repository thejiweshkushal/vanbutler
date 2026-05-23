"""seed system meal: id -2 Cook Holiday

Revision ID: 20260522_0009
Revises: 20260520_0008
Create Date: 2026-05-22

"""

from typing import Sequence, Union

from alembic import op

revision: str = "20260522_0009"
down_revision: Union[str, None] = "20260520_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALL_SLOTS_JSON = '["breakfast", "lunch", "dinner"]'


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO meals (id, name, slot)
        VALUES
            (-2, 'Cook Holiday', '{_ALL_SLOTS_JSON}'::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            slot = EXCLUDED.slot
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM daily_choices WHERE meal_id = -2")
    op.execute("DELETE FROM meals WHERE id = -2")
