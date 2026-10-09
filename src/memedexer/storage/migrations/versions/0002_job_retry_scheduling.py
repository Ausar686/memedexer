"""job retry scheduling, media mime type and optional media size

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 17:05:41.890349
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import memedexer.storage.models

revision: str = '0002'
down_revision: str | Sequence[str] | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('available_at', memedexer.storage.models.UTCDateTime(), nullable=True))
    op.execute('UPDATE jobs SET available_at = created_at')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.alter_column('available_at', existing_type=memedexer.storage.models.UTCDateTime(), nullable=False)
        batch_op.drop_index(batch_op.f('ix_jobs_status_id'))
        batch_op.create_index('ix_jobs_status_available', ['status', 'available_at', 'id'], unique=False)

    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mime_type', sa.String(length=64), nullable=True))
    op.execute("UPDATE media SET mime_type = 'image/jpeg'")
    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.alter_column('mime_type', existing_type=sa.String(length=64), nullable=False)
        batch_op.alter_column('width',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.alter_column('height',
               existing_type=sa.INTEGER(),
               nullable=True)


def downgrade() -> None:
    op.execute('UPDATE media SET width = 0 WHERE width IS NULL')
    op.execute('UPDATE media SET height = 0 WHERE height IS NULL')
    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.alter_column('height',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.alter_column('width',
               existing_type=sa.INTEGER(),
               nullable=False)
        batch_op.drop_column('mime_type')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_status_available')
        batch_op.create_index(batch_op.f('ix_jobs_status_id'), ['status', 'id'], unique=False)
        batch_op.drop_column('available_at')
