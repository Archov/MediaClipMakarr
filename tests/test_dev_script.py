from __future__ import annotations

from pathlib import Path

import pytest

import scripts.dev as dev_script


def test_windows_profile_is_preferred_without_changing_docker_env(monkeypatch, tmp_path) -> None:
    windows_profile = tmp_path / ".env.windows"
    windows_profile.write_text("MCM_DEV_API_PORT=8123\n", encoding="utf-8")
    monkeypatch.setattr(dev_script.sys, "platform", "win32")
    monkeypatch.setattr(dev_script, "WINDOWS_ENV_FILE", windows_profile)

    assert dev_script.development_env_file() == windows_profile


def test_default_env_remains_fallback_when_windows_profile_is_missing(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(dev_script.sys, "platform", "win32")
    monkeypatch.setattr(dev_script, "WINDOWS_ENV_FILE", tmp_path / ".env.windows")
    monkeypatch.setattr(dev_script, "PROJECT_ROOT", Path("project-root"))

    assert dev_script.development_env_file() == Path("project-root/.env")


class _FakeProcess:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.graceful_shutdown_requested = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: int | None = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9


def _finish_gracefully(processes: set[_FakeProcess]) -> None:
    for process in processes:
        process.graceful_shutdown_requested = True
        process.returncode = 0


def test_dev_command_reports_natural_process_failure(monkeypatch) -> None:
    failed_process = _FakeProcess(returncode=1)
    running_process = _FakeProcess(returncode=None)
    processes = iter([failed_process, running_process])

    monkeypatch.setattr(dev_script.shutil, "which", lambda _command: "npm")
    monkeypatch.setattr(
        dev_script.subprocess,
        "Popen",
        lambda *_args, **_kwargs: next(processes),
    )
    monkeypatch.setattr(dev_script, "request_graceful_shutdown", _finish_gracefully)

    with pytest.raises(SystemExit, match=r"stopped unexpectedly: \[1\]"):
        dev_script.main()

    assert failed_process.terminated is False
    assert running_process.graceful_shutdown_requested is True
    assert running_process.terminated is False


def test_dev_command_ctrl_c_gracefully_stops_both_processes(monkeypatch) -> None:
    launched = [_FakeProcess(returncode=None), _FakeProcess(returncode=None)]
    processes = iter(launched)

    def interrupt_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(dev_script.shutil, "which", lambda _command: "node")
    monkeypatch.setattr(
        dev_script.subprocess,
        "Popen",
        lambda *_args, **_kwargs: next(processes),
    )
    monkeypatch.setattr(dev_script, "request_graceful_shutdown", _finish_gracefully)
    monkeypatch.setattr(dev_script.time, "sleep", interrupt_sleep)

    dev_script.main()

    assert all(process.graceful_shutdown_requested for process in launched)
    assert all(not process.terminated for process in launched)
