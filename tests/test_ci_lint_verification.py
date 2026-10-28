"""CI verifies the submitted source without silently repairing its checkout."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_helper_release import workflow


@pytest.mark.parametrize("source,valid", [
    pytest.param("VALUE = 1\n", True, id="clean"),
    pytest.param("import os\n", False, id="fixable-unused-import"),
    pytest.param("VALUE =\n", False, id="syntax-error"),
])
def test_ci_lint_checks_original_source_without_mutation(tmp_path, source, valid):
    job = workflow("validation.yml")["jobs"]["lint"]
    step = next(item for item in job["steps"] if item.get("name") == "Run Ruff linter")
    assert job.get("continue-on-error", "false") == "false"
    assert step.get("continue-on-error", "false") == "false"
    directory = tmp_path / "src"
    directory.mkdir()
    path = directory / "example.py"
    path.write_text(source, encoding="utf-8")
    bash = shutil.which("bash") or "bash"
    if sys.platform == "win32":
        git_bash = Path(shutil.which("git") or "git").parent.parent / "bin/bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    assert path.read_text(encoding="utf-8") == source
    assert ("Ruff checks passed!" in result.stdout) is valid
