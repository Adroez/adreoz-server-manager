"""per-server JVM arguments

Revision ID: 20261112_server_jvm_args
Revises: 20261109_software_install
Create Date: 2026-11-12 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261112_server_jvm_args'
down_revision: str = '20261109_software_install'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('jvm_args', sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.drop_column('jvm_args')
