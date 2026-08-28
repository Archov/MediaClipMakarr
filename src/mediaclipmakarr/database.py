from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import URL, event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command


def upgrade_database(
    database_path: Path,
    alembic_ini_path: Path = Path("alembic.ini"),
    alembic_script_dir: Path = Path("alembic"),
) -> None:
    """Apply migrations synchronously; callers must dispatch this through BlockingIOExecutor."""

    config = Config(str(alembic_ini_path))
    config.set_main_option("script_location", str(alembic_script_dir))
    config.set_main_option("sqlalchemy.url", str(URL.create("sqlite", database=str(database_path))))
    command.upgrade(config, "head")


def create_database_engine(database_path: Path) -> AsyncEngine:
    url = URL.create("sqlite+aiosqlite", database=str(database_path))
    engine = create_async_engine(url, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


async def check_database(engine: AsyncEngine) -> tuple[bool, str | None]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    except Exception:
        return False, None
    return True, str(revision) if revision is not None else None
