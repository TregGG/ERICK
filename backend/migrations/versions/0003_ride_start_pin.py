"""ride start pin

Each new ride gets a four-digit PIN that the passenger tells the driver at the
pickup; the driver needs it to start the trip, so the right passenger boards
the right rickshaw. Rides created before this migration have no PIN and can
start without one.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('rides', sa.Column('start_pin', sa.String(length=4), nullable=True))


def downgrade() -> None:
    op.drop_column('rides', 'start_pin')
