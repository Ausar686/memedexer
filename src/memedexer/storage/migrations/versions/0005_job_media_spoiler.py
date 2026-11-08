"""job media spoiler

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09 00:35:42.665078
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import memedexer.storage.models

revision: str = '0005'
down_revision: str | Sequence[str] | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('has_spoiler', sa.Boolean(), server_default=sa.text('0'), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('has_spoiler')
