"""Path-related configuration and environment detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _merge_vars(base_vars: dict[str, Any], vars: dict[str, Any]) -> None:
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
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# Ensure data directory exists
if not DATA_DIR.exists():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

# Translations path
# NOTE: These don't have to be available to the end-user, so the path points to the internal dir
LANG_PATH = PROJECT_ROOT / "lang"

# Persistent storage paths - use DATA_DIR for Docker compatibility
COOKIES_PATH = DATA_DIR / "cookies.jar"
SETTINGS_PATH = DATA_DIR / "settings.json"
