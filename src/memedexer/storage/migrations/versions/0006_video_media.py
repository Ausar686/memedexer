"""video media

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09 00:53:36.392203
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import memedexer.storage.models

revision: str = '0006'
down_revision: str | Sequence[str] | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('thumbnail_only', sa.Boolean(), server_default=sa.text('0'), nullable=False))

    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.add_column(sa.Column('media_type', sa.Enum('photo', 'image_document', 'video', 'video_document', 'animation', name='mediatype', native_enum=False, length=32), nullable=True))
        batch_op.add_column(sa.Column('duration', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('thumbnail_file_id', sa.String(length=256), nullable=True))
        batch_op.alter_column('photo_file_id', new_column_name='reply_file_id', existing_type=sa.String(length=256))
    # Only images existed so far, and only photos have dimensions at ingestion.
    op.execute("UPDATE media SET media_type = CASE WHEN width IS NOT NULL THEN 'photo' ELSE 'image_document' END")
    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.alter_column('media_type', existing_type=sa.Enum('photo', 'image_document', 'video', 'video_document', 'animation', name='mediatype', native_enum=False, length=32), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('media', schema=None) as batch_op:
        batch_op.alter_column('reply_file_id', new_column_name='photo_file_id', existing_type=sa.String(length=256))
        batch_op.drop_column('thumbnail_file_id')
        batch_op.drop_column('duration')
        batch_op.drop_column('media_type')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_column('thumbnail_only')
