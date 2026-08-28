from __future__ import annotations

import pytest

from mediaclipmakarr.database import check_database, create_database_engine, upgrade_database


@pytest.mark.asyncio
async def test_alembic_initializes_sqlite_database(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        healthy, revision = await check_database(engine)
    finally:
        await engine.dispose()

    assert healthy is True
    assert revision == "0002_jobs_and_clips"
