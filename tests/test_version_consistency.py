import re
from pathlib import Path

import tomllib

import src
from src.version import __version__
from src.web.app import app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_all_project_version_sources_match():
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lockfile = tomllib.loads((PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked_projects = [
        package
        for package in lockfile["package"]
        if package["name"] == pyproject["project"]["name"]
    ]

    assert len(locked_projects) == 1
    assert pyproject["project"]["version"] == __version__
    assert locked_projects[0]["version"] == __version__
    assert src.__version__ == __version__
    assert app.version == __version__


def test_release_scripts_update_and_stage_lockfile():
    for script_name in ("create_release.sh", "revert_release.sh"):
        script = (
            PROJECT_ROOT / ".github" / "scripts" / script_name
        ).read_text(encoding="utf-8")

        assert re.search(r"^\s*uv lock$", script, re.MULTILINE)
        assert re.search(
            r"^\s*git add src/version\.py pyproject\.toml uv\.lock$",
            script,
            re.MULTILINE,
        )


def test_release_rollback_is_published_before_destructive_cleanup():
    script = (PROJECT_ROOT / ".github" / "scripts" / "revert_release.sh").read_text(
        encoding="utf-8"
    )

    assert "command -v uv" in script
    assert script.index("git pull --ff-only origin main") < script.index(
        'PREVIOUS_VERSION=$("$SCRIPT_DIR/get_previous_version.sh"'
    )
    assert script.index("git push origin main") < script.index(
        'gh release delete "$TAG_NAME" --yes'
    )
    assert 'Refusing to revert a non-current historical release' in script
    assert "CLEANUP_FAILED=true" in script
