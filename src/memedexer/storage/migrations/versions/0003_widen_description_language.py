"""widen description language

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 17:59:50.808844
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import memedexer.storage.models

revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('chats', schema=None) as batch_op:
        batch_op.alter_column('description_language',
               existing_type=sa.VARCHAR(length=16),
               type_=sa.String(length=32),
               existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('chats', schema=None) as batch_op:
        batch_op.alter_column('description_language',
               existing_type=sa.String(length=32),
               type_=sa.VARCHAR(length=16),
               existing_nullable=True)
