from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    if npm is None:
        raise SystemExit("Node.js/npm is required. Run `npm run setup` after installing Node.js.")

    environment = os.environ.copy()
    python_path = str(PROJECT_ROOT / "src")
    sys.path.insert(0, python_path)
    from mediaclipmakarr.config import Settings

    settings = Settings()
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (python_path, environment.get("PYTHONPATH", "")) if part
    )
    api_port = str(settings.dev_api_port)
    web_port = str(settings.dev_web_port)
    environment["MCM_DEV_API_PORT"] = api_port
    environment["MCM_DEV_WEB_PORT"] = web_port
    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "mediaclipmakarr.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        api_port,
    ]
    # Uvicorn's Windows reload worker uses a SelectorEventLoop, which cannot host
    # the required asyncio subprocess boundary. Keep the normal ProactorEventLoop.
    if sys.platform != "win32":
        backend_command.extend(["--reload", "--reload-dir", "src"])
    commands = [
        backend_command,
        [npm, "--prefix", "frontend", "run", "dev", "--", "--port", web_port],
    ]
    processes = [
        subprocess.Popen(command, cwd=PROJECT_ROOT, env=environment) for command in commands
    ]
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        terminated = set()
        for process in processes:
            if process.poll() is None:
                terminated.add(process)
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        failed = [
            process.returncode
            for process in processes
            if process not in terminated and process.returncode not in (0, None)
        ]
        if failed:
            raise SystemExit(f"A development process stopped unexpectedly: {failed}")


if __name__ == "__main__":
    main()
