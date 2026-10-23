from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from .config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
async_session_factory = AsyncSessionLocal

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def create_all_tables():
    from . import models  # noqa: ensure all models are imported
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

