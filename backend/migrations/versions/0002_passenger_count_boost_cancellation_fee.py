"""passenger count, per-passenger boost and cancellation fee

Adds the passenger count and per-passenger fare breakdown to rides, a kind to
payments so a cancellation fee can be recorded on a cancelled ride, and
ride_approach_samples (distance to pickup only, no coordinates) used to decide
whether the assigned driver was approaching when the passenger cancelled.

Existing rides were flat-fare single-passenger rides, so their base fare is
backfilled from fare_paise.

SQLite cannot add CHECK constraints to an existing table; there the API
validation applies and the constraints exist only on PostgreSQL.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"
    op.add_column('rides', sa.Column('passenger_count', sa.Integer(), server_default='1', nullable=False))
    op.add_column('rides', sa.Column('base_fare_paise', sa.Integer(), server_default='0', nullable=False))
    op.add_column('rides', sa.Column('boost_paise', sa.Integer(), server_default='0', nullable=False))
    op.add_column('rides', sa.Column('boost_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('rides', sa.Column('cancellation_fee_paise', sa.Integer(), nullable=True))
    op.execute("UPDATE rides SET base_fare_paise = fare_paise")
    op.add_column('payments', sa.Column('kind', sa.String(length=24), server_default='fare', nullable=False))
    if is_postgres:
        op.create_check_constraint('ck_rides_passenger_count', 'rides', 'passenger_count BETWEEN 1 AND 6')
        op.create_check_constraint('ck_rides_boost', 'rides', 'boost_paise >= 0')
        op.create_check_constraint('ck_payments_kind', 'payments', "kind IN ('fare', 'cancellation_fee')")

    op.create_table('ride_approach_samples',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('ride_id', sa.Uuid(), nullable=False),
    sa.Column('distance_m', sa.Integer(), nullable=False),
    sa.Column('accuracy_m', sa.Float(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['ride_id'], ['rides.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ride_approach_samples_ride', 'ride_approach_samples', ['ride_id', 'recorded_at'], unique=False)


def downgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"
    op.drop_index('ix_ride_approach_samples_ride', table_name='ride_approach_samples')
    op.drop_table('ride_approach_samples')
    if is_postgres:
        op.drop_constraint('ck_payments_kind', 'payments', type_='check')
        op.drop_constraint('ck_rides_boost', 'rides', type_='check')
        op.drop_constraint('ck_rides_passenger_count', 'rides', type_='check')
    op.drop_column('payments', 'kind')
    for column in ('cancellation_fee_paise', 'boost_count', 'boost_paise', 'base_fare_paise', 'passenger_count'):
        op.drop_column('rides', column)
