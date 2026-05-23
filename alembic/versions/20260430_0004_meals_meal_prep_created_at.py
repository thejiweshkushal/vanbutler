"""Add created_at to meals and meal_prep.

Revision ID: 20260430_0004
Revises: 20260430_0003
Create Date: 2026-04-30

Existing rows receive the timestamp at migration time via DEFAULT now().
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260430_0004"
down_revision: Union[str, None] = "20260430_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "meals",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "meal_prep",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("meal_prep", "created_at")
    op.drop_column("meals", "created_at")
