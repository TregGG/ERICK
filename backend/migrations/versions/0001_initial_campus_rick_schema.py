"""initial campus rick schema

Replaces the legacy prototype tables (users/rickshaws/ride_requests/rides with a
different shape). The legacy SQLite files contained only demo data, so nothing
is migrated from them; they are left untouched on disk.

On PostgreSQL this also enables PostGIS and adds a generated geography column
with a GiST index on driver_locations for radius searches.

Revision ID: 0001
Revises:
Create Date: 2026-10-25 13:48:44.180716
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_postgres = op.get_bind().dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table('campus_zones',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('geojson', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=300), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind IN ('boundary', 'operating')", name='ck_campus_zones_kind'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('landmarks',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('category', sa.String(length=24), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lng', sa.Float(), nullable=False),
    sa.Column('is_pickup_point', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=200), nullable=False),
    sa.Column('osm_ref', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('otp_challenges',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('phone', sa.String(length=16), nullable=False),
    sa.Column('app_role', sa.String(length=16), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('request_ip', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_otp_challenges_phone_created', 'otp_challenges', ['phone', 'created_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('phone', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=True),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role IN ('passenger', 'driver', 'admin')", name='ck_users_role'),
    sa.CheckConstraint("status IN ('active', 'suspended')", name='ck_users_status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('phone')
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('actor_role', sa.String(length=16), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_type', sa.String(length=32), nullable=True),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_created', 'audit_logs', ['created_at'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(length=200), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('driver_locations',
    sa.Column('driver_id', sa.Uuid(), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lng', sa.Float(), nullable=False),
    sa.Column('accuracy_m', sa.Float(), nullable=False),
    sa.Column('heading', sa.Float(), nullable=True),
    sa.Column('speed_mps', sa.Float(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('inside_campus', sa.Boolean(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("source IN ('gps', 'simulator')", name='ck_driver_locations_source'),
    sa.CheckConstraint('lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180', name='ck_driver_locations_coords'),
    sa.ForeignKeyConstraint(['driver_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('driver_id')
    )
    op.create_table('driver_profiles',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('verification_status', sa.String(length=16), nullable=False),
    sa.Column('verification_note', sa.String(length=200), nullable=True),
    sa.Column('is_online', sa.Boolean(), nullable=False),
    sa.Column('online_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("verification_status IN ('pending', 'approved', 'rejected', 'suspended')", name='ck_driver_profiles_verification'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('saved_places',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lng', sa.Float(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'label', name='uq_saved_places_user_label')
    )
    op.create_table('system_settings',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('vehicles',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('driver_id', sa.Uuid(), nullable=False),
    sa.Column('registration_number', sa.String(length=20), nullable=False),
    sa.Column('capacity', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('capacity BETWEEN 1 AND 6', name='ck_vehicles_capacity'),
    sa.ForeignKeyConstraint(['driver_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('driver_id'),
    sa.UniqueConstraint('registration_number')
    )
    op.create_table('rides',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('passenger_id', sa.Uuid(), nullable=False),
    sa.Column('driver_id', sa.Uuid(), nullable=True),
    sa.Column('vehicle_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('pickup_name', sa.String(length=120), nullable=False),
    sa.Column('pickup_lat', sa.Float(), nullable=False),
    sa.Column('pickup_lng', sa.Float(), nullable=False),
    sa.Column('dropoff_name', sa.String(length=120), nullable=False),
    sa.Column('dropoff_lat', sa.Float(), nullable=False),
    sa.Column('dropoff_lng', sa.Float(), nullable=False),
    sa.Column('route_distance_m', sa.Integer(), nullable=True),
    sa.Column('route_duration_s', sa.Integer(), nullable=True),
    sa.Column('route_source', sa.String(length=24), nullable=True),
    sa.Column('fare_paise', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('offer_count', sa.Integer(), nullable=False),
    sa.Column('search_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('cancel_reason', sa.String(length=200), nullable=True),
    sa.Column('cancelled_by', sa.String(length=16), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('arrived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("driver_id IS NOT NULL OR NOT (status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS'))", name='ck_rides_assigned_has_driver'),
    sa.CheckConstraint("status IN ('REQUESTED', 'OFFERED', 'ACCEPTED', 'ARRIVED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'NO_DRIVER_AVAILABLE')", name='ck_rides_status'),
    sa.CheckConstraint('fare_paise >= 0', name='ck_rides_fare'),
    sa.CheckConstraint('pickup_lat BETWEEN -90 AND 90 AND dropoff_lat BETWEEN -90 AND 90', name='ck_rides_lat'),
    sa.CheckConstraint('pickup_lng BETWEEN -180 AND 180 AND dropoff_lng BETWEEN -180 AND 180', name='ck_rides_lng'),
    sa.ForeignKeyConstraint(['driver_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['passenger_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('passenger_id', 'idempotency_key', name='uq_rides_passenger_idempotency')
    )
    op.create_index('ix_rides_driver_created', 'rides', ['driver_id', 'created_at'], unique=False)
    op.create_index('ix_rides_passenger_created', 'rides', ['passenger_id', 'created_at'], unique=False)
    op.create_index('ix_rides_status', 'rides', ['status'], unique=False)
    op.create_index('uq_rides_one_active_per_driver', 'rides', ['driver_id'], unique=True, postgresql_where=sa.text("status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"), sqlite_where=sa.text("status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"))
    op.create_index('uq_rides_one_active_per_passenger', 'rides', ['passenger_id'], unique=True, postgresql_where=sa.text("status IN ('REQUESTED', 'OFFERED', 'ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"), sqlite_where=sa.text("status IN ('REQUESTED', 'OFFERED', 'ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"))
    op.create_table('notifications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('body', sa.String(length=300), nullable=True),
    sa.Column('ride_id', sa.Uuid(), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['ride_id'], ['rides.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notifications_user_created', 'notifications', ['user_id', 'created_at'], unique=False)
    op.create_table('payments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('ride_id', sa.Uuid(), nullable=False),
    sa.Column('amount_paise', sa.Integer(), nullable=False),
    sa.Column('method', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('collected_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("method IN ('cash')", name='ck_payments_method'),
    sa.CheckConstraint("status IN ('PENDING', 'PAID', 'WAIVED')", name='ck_payments_status'),
    sa.CheckConstraint('amount_paise >= 0', name='ck_payments_amount'),
    sa.ForeignKeyConstraint(['collected_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ride_id'], ['rides.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ride_id')
    )
    op.create_table('ratings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('ride_id', sa.Uuid(), nullable=False),
    sa.Column('rater_id', sa.Uuid(), nullable=False),
    sa.Column('ratee_id', sa.Uuid(), nullable=False),
    sa.Column('stars', sa.SmallInteger(), nullable=False),
    sa.Column('comment', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('stars BETWEEN 1 AND 5', name='ck_ratings_stars'),
    sa.ForeignKeyConstraint(['ratee_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['rater_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ride_id'], ['rides.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ride_id', 'rater_id', name='uq_ratings_ride_rater')
    )
    op.create_index('ix_ratings_ratee', 'ratings', ['ratee_id'], unique=False)
    op.create_table('ride_events',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('ride_id', sa.Uuid(), nullable=False),
    sa.Column('from_status', sa.String(length=24), nullable=True),
    sa.Column('to_status', sa.String(length=24), nullable=False),
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('actor_role', sa.String(length=16), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['ride_id'], ['rides.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ride_events_ride', 'ride_events', ['ride_id', 'id'], unique=False)
    op.create_table('ride_offers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('ride_id', sa.Uuid(), nullable=False),
    sa.Column('driver_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('distance_to_pickup_m', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'EXPIRED', 'CANCELLED')", name='ck_ride_offers_status'),
    sa.ForeignKeyConstraint(['driver_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ride_id'], ['rides.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ride_id', 'driver_id', name='uq_ride_offers_ride_driver')
    )
    op.create_index('uq_ride_offers_one_pending_per_driver', 'ride_offers', ['driver_id'], unique=True, postgresql_where=sa.text("status = 'PENDING'"), sqlite_where=sa.text("status = 'PENDING'"))
    op.create_index('uq_ride_offers_one_pending_per_ride', 'ride_offers', ['ride_id'], unique=True, postgresql_where=sa.text("status = 'PENDING'"), sqlite_where=sa.text("status = 'PENDING'"))
    if is_postgres:
        op.execute(
            "ALTER TABLE driver_locations ADD COLUMN geog geography(Point, 4326) "
            "GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography) STORED"
        )
        op.execute("CREATE INDEX ix_driver_locations_geog ON driver_locations USING GIST (geog)")


def downgrade() -> None:
    op.drop_index('uq_ride_offers_one_pending_per_ride', table_name='ride_offers', postgresql_where=sa.text("status = 'PENDING'"), sqlite_where=sa.text("status = 'PENDING'"))
    op.drop_index('uq_ride_offers_one_pending_per_driver', table_name='ride_offers', postgresql_where=sa.text("status = 'PENDING'"), sqlite_where=sa.text("status = 'PENDING'"))
    op.drop_table('ride_offers')
    op.drop_index('ix_ride_events_ride', table_name='ride_events')
    op.drop_table('ride_events')
    op.drop_index('ix_ratings_ratee', table_name='ratings')
    op.drop_table('ratings')
    op.drop_table('payments')
    op.drop_index('ix_notifications_user_created', table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('uq_rides_one_active_per_passenger', table_name='rides', postgresql_where=sa.text("status IN ('REQUESTED', 'OFFERED', 'ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"), sqlite_where=sa.text("status IN ('REQUESTED', 'OFFERED', 'ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"))
    op.drop_index('uq_rides_one_active_per_driver', table_name='rides', postgresql_where=sa.text("status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"), sqlite_where=sa.text("status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS')"))
    op.drop_index('ix_rides_status', table_name='rides')
    op.drop_index('ix_rides_passenger_created', table_name='rides')
    op.drop_index('ix_rides_driver_created', table_name='rides')
    op.drop_table('rides')
    op.drop_table('vehicles')
    op.drop_table('system_settings')
    op.drop_table('saved_places')
    op.drop_table('driver_profiles')
    op.drop_table('driver_locations')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index('ix_audit_logs_created', table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('users')
    op.drop_index('ix_otp_challenges_phone_created', table_name='otp_challenges')
    op.drop_table('otp_challenges')
    op.drop_table('landmarks')
    op.drop_table('campus_zones')
