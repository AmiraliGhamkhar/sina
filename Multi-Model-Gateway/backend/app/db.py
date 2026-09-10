from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from  sqlalchemy.orm import declarative_base
from app.core.config import settings

DATABASE_URL = settings.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.DEBUG,
    # Sized for concurrent streams. The chat path now releases its connection
    # before streaming (see routers/chat.py), so these mostly serve short reads
    # /writes; pre_ping drops connections Postgres closed out from under us.
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(engine, 
                                       expire_on_commit=False, 
                                       class_=AsyncSession)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

