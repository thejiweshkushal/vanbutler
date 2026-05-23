"""trivia and trivia_embeddings tables

Revision ID: 20260518_0007
Revises: 20260508_0006
Create Date: 2026-05-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260518_0007"
down_revision: Union[str, None] = "20260508_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trivia",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("trivia", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_sent_on", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trivia_category", "trivia", ["category"], unique=False)

    op.create_table(
        "trivia_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trivia_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_type", sa.String(length=32), nullable=False),
        sa.Column("embedding", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["trivia_id"], ["trivia.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trivia_embeddings_trivia_id", "trivia_embeddings", ["trivia_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_trivia_embeddings_trivia_id", table_name="trivia_embeddings")
    op.drop_table("trivia_embeddings")
    op.drop_index("ix_trivia_category", table_name="trivia")
    op.drop_table("trivia")
