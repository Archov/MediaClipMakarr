from __future__ import annotations

import pytest

from mediaclipmakarr.config import Settings, validate_path_layout


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
