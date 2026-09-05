from __future__ import annotations

import logging

import pytest

from mediaclipmakarr.database import check_database, create_database_engine, upgrade_database


@pytest.mark.asyncio
async def test_alembic_initializes_sqlite_database(tmp_path) -> None:
    database_path = tmp_path / "application.db"
    startup_logger = logging.getLogger("mediaclipmakarr.startup-test")
    startup_logger.disabled = False
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        healthy, revision = await check_database(engine)
    finally:
        await engine.dispose()

    assert healthy is True
    assert revision == "0010_trim_parent_link"
    assert startup_logger.disabled is False
