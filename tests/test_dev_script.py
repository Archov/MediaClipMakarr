from __future__ import annotations

import pytest

import scripts.dev as dev_script


class _FakeProcess:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: int | None = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9


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

    with pytest.raises(SystemExit, match=r"stopped unexpectedly: \[1\]"):
        dev_script.main()

    assert failed_process.terminated is False
    assert running_process.terminated is True
