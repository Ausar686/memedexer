"""reply delivery

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09 18:34:12.526738
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import memedexer.storage.models

revision: str = '0004'
down_revision: str | Sequence[str] | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_error', sa.Text(), nullable=True))

    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.add_column(sa.Column('photo_file_id', sa.String(length=256), nullable=True))
    # Only photos have dimensions at ingestion, and a photo's own file id is what sendPhoto needs.
    op.execute('UPDATE media SET photo_file_id = file_id WHERE width IS NOT NULL')


def downgrade() -> None:
    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.drop_column('photo_file_id')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('delivery_error')
