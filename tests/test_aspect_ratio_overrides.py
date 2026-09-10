from __future__ import annotations

import pytest

from mediaclipmakarr.aspect_ratio_overrides import (
    AspectRatioIdentity,
    clear_aspect_ratio_override,
    lookup_aspect_ratio_override,
    set_aspect_ratio_override,
)
from mediaclipmakarr.database import create_database_engine, upgrade_database


@pytest.fixture
def engine(tmp_path):
    database_path = tmp_path / "application.db"
    upgrade_database(database_path)
    return create_database_engine(database_path)


@pytest.mark.asyncio
async def test_lookup_returns_none_when_nothing_is_stored(engine) -> None:
    identity = AspectRatioIdentity(path_key="/media/anime/Frieren")

    assert await lookup_aspect_ratio_override(engine, identity) is None


@pytest.mark.asyncio
async def test_set_then_lookup_by_the_same_path_key(engine) -> None:
    identity = AspectRatioIdentity(path_key="/media/anime/Frieren")

    await set_aspect_ratio_override(engine, identity, "4:3")

    assert await lookup_aspect_ratio_override(engine, identity) == "4:3"


@pytest.mark.asyncio
async def test_external_id_match_wins_over_path_key_and_rating_key(engine) -> None:
    # Stored via an external id only.
    await set_aspect_ratio_override(
        engine, AspectRatioIdentity(external_ids=["imdb://tt1234567"]), "2.35:1"
    )
    # A lookup carrying a *different* path_key/rating_key but the matching
    # external id should still hit — external ids are the highest-priority,
    # most durable identity.
    lookup_identity = AspectRatioIdentity(
        external_ids=["imdb://tt1234567"],
        path_key="/media/movies/Some Other Folder (2020)",
        plex_rating_key="999",
    )

    assert await lookup_aspect_ratio_override(engine, lookup_identity) == "2.35:1"


@pytest.mark.asyncio
async def test_path_key_match_wins_over_rating_key_when_no_external_id_matches(engine) -> None:
    await set_aspect_ratio_override(
        engine, AspectRatioIdentity(path_key="/media/anime/Frieren"), "4:3"
    )
    lookup_identity = AspectRatioIdentity(
        path_key="/media/anime/Frieren", plex_rating_key="does-not-match"
    )

    assert await lookup_aspect_ratio_override(engine, lookup_identity) == "4:3"


@pytest.mark.asyncio
async def test_rating_key_is_the_last_resort(engine) -> None:
    await set_aspect_ratio_override(
        engine, AspectRatioIdentity(plex_rating_key="501"), "16:9"
    )
    lookup_identity = AspectRatioIdentity(path_key="/unrelated/path", plex_rating_key="501")

    assert await lookup_aspect_ratio_override(engine, lookup_identity) == "16:9"


@pytest.mark.asyncio
async def test_set_merges_new_identity_facts_onto_an_existing_record(engine) -> None:
    # First seen with only a path key (e.g. Plex wasn't reachable for Guids yet).
    await set_aspect_ratio_override(
        engine, AspectRatioIdentity(path_key="/media/anime/Frieren"), "4:3"
    )
    # Later seen again with an external id too — should merge onto the same
    # record rather than creating a second, competing one.
    await set_aspect_ratio_override(
        engine,
        AspectRatioIdentity(
            external_ids=["imdb://tt1234567"], path_key="/media/anime/Frieren"
        ),
        "4:3",
    )

    # Now a lookup by *only* the external id (path key unavailable this time)
    # still finds the merged record.
    assert (
        await lookup_aspect_ratio_override(
            engine, AspectRatioIdentity(external_ids=["imdb://tt1234567"])
        )
        == "4:3"
    )


@pytest.mark.asyncio
async def test_set_overwrites_the_stored_ratio_on_a_matching_record(engine) -> None:
    identity = AspectRatioIdentity(path_key="/media/anime/Frieren")
    await set_aspect_ratio_override(engine, identity, "4:3")

    await set_aspect_ratio_override(engine, identity, "16:9")

    assert await lookup_aspect_ratio_override(engine, identity) == "16:9"


@pytest.mark.asyncio
async def test_clear_removes_a_stored_override_and_reports_it_existed(engine) -> None:
    identity = AspectRatioIdentity(path_key="/media/anime/Frieren")
    await set_aspect_ratio_override(engine, identity, "4:3")

    removed = await clear_aspect_ratio_override(engine, identity)

    assert removed is True
    assert await lookup_aspect_ratio_override(engine, identity) is None


@pytest.mark.asyncio
async def test_clear_is_a_no_op_when_nothing_is_stored(engine) -> None:
    identity = AspectRatioIdentity(path_key="/media/anime/Frieren")

    assert await clear_aspect_ratio_override(engine, identity) is False
