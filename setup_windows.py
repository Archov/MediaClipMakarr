#!/usr/bin/env python3
"""Open the MediaClipMakarr Windows configuration wizard."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def main() -> None:
    from scripts.windows_setup import main as wizard_main

    wizard_main()

if __name__ == "__main__":
    main()
