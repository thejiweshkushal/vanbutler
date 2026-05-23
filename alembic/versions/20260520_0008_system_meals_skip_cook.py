"""seed system meals: id 0 None, id -1 Cook Not Coming

Revision ID: 20260520_0008
Revises: 20260518_0007
Create Date: 2026-05-20

"""

from typing import Sequence, Union

from alembic import op

revision: str = "20260520_0008"
down_revision: Union[str, None] = "20260518_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALL_SLOTS_JSON = '["breakfast", "lunch", "dinner"]'


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO meals (id, name, slot)
        VALUES
            (0, 'None', '{_ALL_SLOTS_JSON}'::jsonb),
            (-1, 'Cook Not Coming', '{_ALL_SLOTS_JSON}'::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            slot = EXCLUDED.slot
        """
    )
    # Keep serial sequence above any positive user meal ids.
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('meals', 'id'),
            GREATEST(
                (SELECT COALESCE(MAX(id), 1) FROM meals WHERE id > 0),
                1
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM daily_choices WHERE meal_id IN (0, -1)")
    op.execute("DELETE FROM meals WHERE id IN (0, -1)")
