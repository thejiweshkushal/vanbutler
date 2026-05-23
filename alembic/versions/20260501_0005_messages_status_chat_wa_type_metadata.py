"""messages: status, chat_id, wa_type, status_metadata

Revision ID: 20260501_0005
Revises: 20260430_0004
Create Date: 2026-05-01

Existing rows receive status='received' and nullable chat_id / wa_type / status_metadata.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260501_0005"
down_revision: Union[str, None] = "20260430_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "status",
            sa.String(length=64),
            nullable=False,
            server_default="received",
        ),
    )
    op.add_column(
        "messages",
        sa.Column("chat_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("wa_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("status_metadata", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "status_metadata")
    op.drop_column("messages", "wa_type")
    op.drop_column("messages", "chat_id")
    op.drop_column("messages", "status")
