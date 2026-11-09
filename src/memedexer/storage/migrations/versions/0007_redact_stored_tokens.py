"""redact bot tokens from stored errors

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09 12:00:00.000000
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | Sequence[str] | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Failed file downloads used to store the file URL, which embeds the bot token.
_TOKEN_IN_URL = re.compile(r"/bot\d+:[\w-]+")
_REDACTED = "/bot<redacted>"


def upgrade() -> None:
    connection = op.get_bind()
    jobs = sa.table('jobs', sa.column('id'), sa.column('error'), sa.column('delivery_error'))
    for column in (jobs.c.error, jobs.c.delivery_error):
        rows = connection.execute(sa.select(jobs.c.id, column).where(column.like('%/bot%:%'))).all()
        for job_id, text in rows:
            connection.execute(
                sa.update(jobs).where(jobs.c.id == job_id).values({column: _TOKEN_IN_URL.sub(_REDACTED, text)})
            )


def downgrade() -> None:
    pass
