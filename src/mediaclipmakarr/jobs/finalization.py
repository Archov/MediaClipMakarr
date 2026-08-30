"""Atomic rendered-file installation and interrupted-install recovery."""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def install_rendered_clip(temp_path: Path, destination: Path, preserve_workdir: bool = False) -> None:
    destination = destination.resolve(strict=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError("The resolved clip destination already exists.")
    temp_path.replace(destination)
    if preserve_workdir:
        logger.warning("Preserving completed media job work directory: %s", temp_path.parent)
    else:
        shutil.rmtree(temp_path.parent, ignore_errors=True)


def recover_pending_installation(
    temp_path: Path, destination: Path, preserve_workdir: bool = False
) -> bool:
    destination = destination.resolve(strict=False)
    if destination.exists():
        if preserve_workdir:
            logger.warning("Preserving recovered media job work directory: %s", temp_path.parent)
        else:
            shutil.rmtree(temp_path.parent, ignore_errors=True)
        return True
    if not temp_path.exists():
        return False
    install_rendered_clip(temp_path, destination, preserve_workdir)
    return True

__all__ = ["install_rendered_clip", "recover_pending_installation"]
