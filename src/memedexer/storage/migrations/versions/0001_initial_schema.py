"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-09 16:22:22.865643
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import memedexer.storage.models

revision: str = '0001'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('chats',
    sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('is_forum', sa.Boolean(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'approved', 'rejected', 'revoked', name='chatstatus', native_enum=False, length=32), nullable=False),
    sa.Column('added_by_user_id', sa.BigInteger(), nullable=True),
    sa.Column('description_language', sa.String(length=16), nullable=True),
    sa.Column('provider', sa.String(length=32), nullable=True),
    sa.Column('model', sa.String(length=128), nullable=True),
    sa.Column('created_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chats'))
    )
    op.create_table('media',
    sa.Column('file_unique_id', sa.String(length=64), nullable=False),
    sa.Column('file_id', sa.String(length=256), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=True),
    sa.Column('created_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('file_unique_id', name=op.f('pk_media'))
    )
    op.create_table('captions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('file_unique_id', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('languages', sa.JSON(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('created_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['file_unique_id'], ['media.file_unique_id'], name=op.f('fk_captions_file_unique_id_media')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_captions'))
    )
    with op.batch_alter_table('captions', schema=None) as batch_op:
        batch_op.create_index('ix_captions_media_created', ['file_unique_id', 'created_at'], unique=False)

    op.create_table('topics',
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('thread_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('captioning_enabled', sa.Boolean(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=True),
    sa.Column('model', sa.String(length=128), nullable=True),
    sa.Column('created_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], name=op.f('fk_topics_chat_id_chats'), onupdate='CASCADE', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('chat_id', 'thread_id', name=op.f('pk_topics'))
    )
    op.create_table('jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('thread_id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('file_unique_id', sa.String(length=64), nullable=False),
    sa.Column('trigger', sa.Enum('message', 'edit', 'recaption', name='jobtrigger', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'done', 'failed', 'refused', 'budget_exceeded', name='jobstatus', native_enum=False, length=32), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('caption_id', sa.Integer(), nullable=True),
    sa.Column('reply_message_id', sa.Integer(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_microusd', sa.BigInteger(), nullable=False),
    sa.Column('finished_at', memedexer.storage.models.UTCDateTime(), nullable=True),
    sa.Column('created_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.Column('updated_at', memedexer.storage.models.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['caption_id'], ['captions.id'], name=op.f('fk_jobs_caption_id_captions')),
    sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], name=op.f('fk_jobs_chat_id_chats'), onupdate='CASCADE', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['file_unique_id'], ['media.file_unique_id'], name=op.f('fk_jobs_file_unique_id_media')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_chat_finished', ['chat_id', 'finished_at'], unique=False)
        batch_op.create_index('ix_jobs_chat_message', ['chat_id', 'message_id'], unique=False)
        batch_op.create_index('ix_jobs_status_id', ['status', 'id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_status_id')
        batch_op.drop_index('ix_jobs_chat_message')
        batch_op.drop_index('ix_jobs_chat_finished')

    op.drop_table('jobs')
    op.drop_table('topics')
    with op.batch_alter_table('captions', schema=None) as batch_op:
        batch_op.drop_index('ix_captions_media_created')

    op.drop_table('captions')
    op.drop_table('media')
    op.drop_table('chats')
