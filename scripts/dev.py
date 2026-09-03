from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from watchfiles import watch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ENV_FILE = PROJECT_ROOT / ".env.windows"
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 5


def development_env_file() -> Path:
    if sys.platform == "win32" and WINDOWS_ENV_FILE.is_file():
        return WINDOWS_ENV_FILE
    return PROJECT_ROOT / ".env"


def wait_for_graceful_shutdown(processes: list[subprocess.Popen[bytes]]) -> None:
    """Allow children that received Ctrl+C to finish their own cleanup."""

    deadline = time.monotonic() + GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            break


def request_graceful_shutdown(processes: set[subprocess.Popen[bytes]]) -> None:
    """Send Ctrl+C semantics to each isolated child process group."""

    for process in processes:
        if process.poll() is not None:
            continue
        with contextlib.suppress(OSError):
            if sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGINT)


def force_stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop a child process group that exceeded the graceful shutdown timeout."""

    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
            return
    process.kill()


def main() -> None:
    node = shutil.which("node.exe" if sys.platform == "win32" else "node")
    if node is None:
        raise SystemExit("Node.js is required. Run `npm run setup` after installing Node.js.")

    environment = os.environ.copy()
    python_path = str(PROJECT_ROOT / "src")
    sys.path.insert(0, python_path)
    from mediaclipmakarr.config import ENV_FILE_VARIABLE, Settings

    env_file = development_env_file()
    if sys.platform == "win32" and env_file != WINDOWS_ENV_FILE:
        print(
            "Windows profile not found; using .env. Run `python setup_windows.py` "
            "to create an isolated Windows configuration."
        )
    settings = Settings(_env_file=env_file)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (python_path, environment.get("PYTHONPATH", "")) if part
    )
    environment[ENV_FILE_VARIABLE] = str(env_file)
    # Deployment .env files use container paths for Alembic. Development always
    # runs the repository's migration configuration, regardless of host platform.
    environment["MCM_ALEMBIC_INI_PATH"] = str(PROJECT_ROOT / "alembic.ini")
    environment["MCM_ALEMBIC_SCRIPT_DIR"] = str(PROJECT_ROOT / "alembic")
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
    # the required asyncio subprocess boundary. Keep the normal ProactorEventLoop
    # and instead watch src/ ourselves, restarting the whole backend process on
    # change (see watch_backend_source below). Elsewhere uvicorn's own --reload
    # already does this in-process and more cheaply.
    watch_backend_externally = sys.platform == "win32"
    if not watch_backend_externally:
        backend_command.extend(["--reload", "--reload-dir", "src"])
    frontend_command = [
        node,
        str(PROJECT_ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"),
        "--host",
        "127.0.0.1",
        "--port",
        web_port,
    ]
    process_group_options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )

    def spawn(command: list[str], cwd: Path) -> subprocess.Popen[bytes]:
        return subprocess.Popen(command, cwd=cwd, env=environment, **process_group_options)

    backend_process = spawn(backend_command, PROJECT_ROOT)
    frontend_process = spawn(frontend_command, PROJECT_ROOT / "frontend")

    restart_requested = threading.Event()
    stop_watching = threading.Event()
    watcher_thread: threading.Thread | None = None
    if watch_backend_externally:

        def watch_backend_source() -> None:
            for _ in watch(PROJECT_ROOT / "src", stop_event=stop_watching):
                restart_requested.set()

        watcher_thread = threading.Thread(target=watch_backend_source, daemon=True)
        watcher_thread.start()
        print("Watching src/ for changes; the backend restarts automatically on save.")

    interrupted = False
    try:
        while True:
            if frontend_process.poll() is not None or backend_process.poll() is not None:
                break
            if restart_requested.is_set():
                restart_requested.clear()
                print("Backend source changed; restarting uvicorn...")
                request_graceful_shutdown({backend_process})
                wait_for_graceful_shutdown([backend_process])
                if backend_process.poll() is None:
                    force_stop_process_tree(backend_process)
                    backend_process.wait()
                backend_process = spawn(backend_command, PROJECT_ROOT)
                continue
            time.sleep(0.25)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        stop_watching.set()
        processes = [backend_process, frontend_process]
        shutdown_targets = {process for process in processes if process.poll() is None}
        request_graceful_shutdown(shutdown_targets)
        wait_for_graceful_shutdown(processes)
        for process in processes:
            if process.poll() is None:
                force_stop_process_tree(process)
                process.wait()
        if watcher_thread is not None:
            watcher_thread.join(timeout=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS)
        failed = [
            process.returncode
            for process in processes
            if process not in shutdown_targets and process.returncode not in (0, None)
        ]
        if failed and not interrupted:
            raise SystemExit(f"A development process stopped unexpectedly: {failed}")


if __name__ == "__main__":
    main()
