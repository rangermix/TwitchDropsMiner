"""Path-related configuration and environment detection."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


# Type alias for path operations
JsonType = dict[str, Any]


# Environment detection
IS_DOCKER = os.getenv("DOCKER_ENV") == "1" or os.path.exists("/.dockerenv")

# Site-packages venv path changes depending on the system platform
if sys.platform == "win32":
    SYS_SITE_PACKAGES = "Lib/site-packages"
else:
    # On Linux, the site-packages path includes a versioned 'pythonX.Y' folder part
    # The Lib folder is also spelled in lowercase: 'lib'
    version_info = sys.version_info
    SYS_SITE_PACKAGES = f"lib/python{version_info.major}.{version_info.minor}/site-packages"

# Scripts venv path changes depending on the system platform
if sys.platform == "win32":
    SYS_SCRIPTS = "Scripts"
else:
    SYS_SCRIPTS = "bin"


def _resource_path(relative_path: Path | str) -> Path:
    """
    Get an absolute path to a bundled resource.
    """
    base_path = WORKING_DIR
    return base_path.joinpath(relative_path)


def _merge_vars(base_vars: JsonType, vars: JsonType) -> None:
    """
    Merge variables recursively.

    NOTE: This modifies base_vars in place.
    """
    for k, v in vars.items():
        if k not in base_vars:
            base_vars[k] = v
        elif isinstance(v, dict):
            if isinstance(base_vars[k], dict):
                _merge_vars(base_vars[k], v)
            elif base_vars[k] is Ellipsis:
                # unspecified base, use the passed in var
                base_vars[k] = v
            else:
                raise RuntimeError(f"Var is a dict, base is not: '{k}'")
        elif isinstance(base_vars[k], dict):
            raise RuntimeError(f"Base is a dict, var is not: '{k}'")
        else:
            # simple overwrite
            base_vars[k] = v
    # ensure none of the vars are ellipsis (unset value)
    for k, v in base_vars.items():
        if v is Ellipsis:
            raise RuntimeError(f"Unspecified variable: '{k}'")


# Base Paths - environment-specific resolution
if IS_DOCKER:
    # Docker environment: use fixed paths
    SELF_PATH = Path("/app/main.py")
    WORKING_DIR = Path("/app")
    DATA_DIR = Path("/app/data")

# Ensure data directory exists
if not DATA_DIR.exists():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

# Development paths
VENV_PATH = Path(WORKING_DIR, "env")
SITE_PACKAGES_PATH = Path(VENV_PATH, SYS_SITE_PACKAGES)
SCRIPTS_PATH = Path(VENV_PATH, SYS_SCRIPTS)

# Translations path
# NOTE: These don't have to be available to the end-user, so the path points to the internal dir
LANG_PATH = _resource_path("lang")

# Persistent storage paths - use DATA_DIR for Docker compatibility
LOG_PATH = Path(DATA_DIR, "log.txt")
DUMP_PATH = Path(DATA_DIR, "dump.dat")
LOCK_PATH = Path(DATA_DIR, "lock.file")
CACHE_PATH = Path(DATA_DIR, "cache")
CACHE_DB = Path(CACHE_PATH, "mapping.json")
COOKIES_PATH = Path(DATA_DIR, "cookies.jar")
SETTINGS_PATH = Path(DATA_DIR, "settings.json")
