"""llm_logs: comprehensive request/response log for every LLM call

Revision ID: 20260508_0006
Revises: 20260501_0005
Create Date: 2026-05-08

Adds a single ``llm_logs`` table written best-effort by
``llm.llm_service._persist_llm_log`` after each Gemini ``generate_content``
invocation (both happy path and exceptions).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260508_0006"
down_revision: Union[str, None] = "20260501_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("response_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("request_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "attempt", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_logs_kind_created_at",
        "llm_logs",
        ["kind", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_logs_kind_created_at", table_name="llm_logs")
    op.drop_table("llm_logs")
