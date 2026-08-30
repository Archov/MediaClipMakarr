from __future__ import annotations

import pytest

from mediaclipmakarr.config import Settings, validate_path_layout


def test_media_preparation_timeout_is_separate_from_short_tool_timeout() -> None:
    settings = Settings(
        _env_file=None,
        subprocess_timeout_seconds=3,
        media_preparation_timeout_seconds=180,
    )

    assert settings.subprocess_timeout_seconds == 3
    assert settings.media_preparation_timeout_seconds == 180


def test_job_workdir_preservation_is_disabled_by_default() -> None:
    assert Settings(_env_file=None).preserve_job_workdirs is False


def test_job_workdir_preservation_can_be_enabled_with_environment(monkeypatch) -> None:
    monkeypatch.setenv("MCM_PRESERVE_JOB_WORKDIRS", "true")

    assert Settings(_env_file=None).preserve_job_workdirs is True


def test_writable_directories_cannot_overlap_source_media(tmp_path) -> None:
    source = tmp_path / "source"
    settings = Settings(
        private_data_dir=tmp_path / "private",
        work_dir=source / "work",
        clip_dir=tmp_path / "clips",
        source_dirs=[source],
    )

    with pytest.raises(ValueError, match="must not overlap a source directory"):
        validate_path_layout(settings)


@pytest.mark.parametrize("field", ["database_filename", "process_lock_filename"])
@pytest.mark.parametrize("unsafe_filename", ["../outside", "C:outside"])
def test_application_data_filenames_cannot_escape_private_directory(
    field, unsafe_filename
) -> None:
    with pytest.raises(ValueError, match="must be plain filenames"):
        Settings(_env_file=None, **{field: unsafe_filename})
