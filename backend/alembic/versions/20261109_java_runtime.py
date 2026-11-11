"""java runtime management

Revision ID: 20261109_java_runtime
Revises: 20261108_backup_system
Create Date: 2026-11-09 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20261109_java_runtime'
down_revision: str = '20261108_backup_system'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'java_runtimes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('vendor', sa.String(length=64), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False),
        sa.Column('major_version', sa.Integer(), nullable=False),
        sa.Column('executable_path', sa.String(length=512), nullable=False),
        sa.Column('source_url', sa.String(length=512), nullable=True),
        sa.Column('checksum', sa.String(length=128), nullable=True),
        sa.Column('checksum_algorithm', sa.String(length=16), nullable=True),
        sa.Column('install_status', sa.String(length=32), nullable=False, server_default='installed'),
        sa.Column('installed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_managed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_java_runtimes_executable_path'), 'java_runtimes', ['executable_path'], unique=True)
    op.create_index(op.f('ix_java_runtimes_is_managed'), 'java_runtimes', ['is_managed'], unique=False)
    op.create_index(op.f('ix_java_runtimes_install_status'), 'java_runtimes', ['install_status'], unique=False)

    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('java_runtime_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f('fk_servers_java_runtime_id_java_runtimes'),
            'java_runtimes',
            ['java_runtime_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_index(
            batch_op.f('ix_servers_java_runtime_id'), ['java_runtime_id'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_servers_java_runtime_id_java_runtimes'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_servers_java_runtime_id'))
        batch_op.drop_column('java_runtime_id')

    op.drop_index(op.f('ix_java_runtimes_install_status'))
    op.drop_index(op.f('ix_java_runtimes_is_managed'))
    op.drop_index(op.f('ix_java_runtimes_executable_path'))
    op.drop_table('java_runtimes')
