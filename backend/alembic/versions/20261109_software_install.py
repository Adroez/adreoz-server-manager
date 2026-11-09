"""server software installation

Revision ID: 20261109_software_install
Revises: 20261109_java_runtime
Create Date: 2026-11-09 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261109_software_install'
down_revision: str = '20261109_java_runtime'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('software_version', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('install_status', sa.String(length=32), nullable=False, server_default='pending'))
        batch_op.add_column(sa.Column('install_error', sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.drop_column('install_error')
        batch_op.drop_column('install_status')
        batch_op.drop_column('software_version')
