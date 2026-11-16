"""The image reaps its own child processes, whatever flags the container runs with."""

import json
from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_image_runs_python_under_an_init_process():
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    entrypoints = [line for line in lines if line.startswith("ENTRYPOINT ")]
    commands = [line for line in lines if line.startswith("CMD ")]

    assert len(entrypoints) == 1
    assert json.loads(entrypoints[0].removeprefix("ENTRYPOINT ")) == ["/sbin/tini", "-s", "--"]
    assert json.loads(commands[-1].removeprefix("CMD ")) == ["python", "main.py"]
    assert any("apk add" in line and "tini" in line.split() for line in lines)


def test_healthcheck_timeout_cannot_orphan_its_probe():
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    probe = next(line.strip() for line in lines if "/healthz" in line)

    # Shell form would make the timed-out shell's python child an orphan.
    assert json.loads(probe.removeprefix("CMD "))[:2] == ["python", "-c"]
