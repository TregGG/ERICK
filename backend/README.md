# Campus E-Rickshaw Ride Matching Backend

This is the FastAPI backend for the Campus E-Rickshaw platform.

## Setup Instructions

1. **Install Dependencies**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Database Setup**
   Ensure PostgreSQL is installed with the PostGIS extension.
   ```sql
   CREATE DATABASE erickshaw;
   \c erickshaw;
   CREATE EXTENSION postgis;
   CREATE EXTENSION "uuid-ossp";
   ```

3. **Environment Variables**
   Copy `.env.example` to `.env` and fill in the values.
   ```bash
   cp .env.example .env
   ```

4. **Run Migrations**
   Initialize Alembic and run migrations.
   ```bash
   alembic init alembic
   # Configure alembic.ini with sqlalchemy.url
   alembic revision --autogenerate -m "Initial setup"
   alembic upgrade head
   ```

5. **Start Server**
   ```bash
   uvicorn backend.main:app --reload
   ```

## Architecture
- FastAPI for REST and WebSockets
- SQLAlchemy (async) for ORM
- GeoAlchemy2 + PostGIS for spatial queries (geofencing, routing)
- Redis for fast queued matching and caching (Phase 3)
