from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if npm is None:
        raise SystemExit("Node.js/npm is required. Install Node.js 22 or newer and try again.")
    run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"])
    run([npm, "--prefix", "frontend", "install"])


if __name__ == "__main__":
    main()
