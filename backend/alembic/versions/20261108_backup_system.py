"""backup system

Revision ID: 20261108_backup_system
Revises: 20261108_server_lifecycle
Create Date: 2026-11-08 17:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '20261108_backup_system'
down_revision: str = '20261108_server_lifecycle'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'server_backups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('unique_id', sa.String(length=36), nullable=False),
        sa.Column('filename', sa.String(length=256), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error_message', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('server_backups', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_server_backups_owner_id'), ['owner_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_server_backups_server_id'), ['server_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_server_backups_unique_id'), ['unique_id'], unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_server_backups_status'), ['status'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_server_backups_created_at'), ['created_at'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('server_backups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_server_backups_created_at'))
        batch_op.drop_index(batch_op.f('ix_server_backups_status'))
        batch_op.drop_index(batch_op.f('ix_server_backups_unique_id'))
        batch_op.drop_index(batch_op.f('ix_server_backups_server_id'))
        batch_op.drop_index(batch_op.f('ix_server_backups_owner_id'))

    op.drop_table('server_backups')
