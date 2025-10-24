#!/usr/bin/env python3
"""Add language_name field to all translation JSON files."""

import json
from pathlib import Path

LANG_PATH = Path(__file__).parent / "lang"

def add_language_names():
    """Add language_name field to each translation file based on filename."""
    for filepath in LANG_PATH.glob("*.json"):
        # Extract language name from filename (without .json extension)
        language_name = filepath.stem

        print(f"Processing {filepath.name}...")

        # Read the JSON file
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Add language_name at the beginning
        updated_data = {"language_name": language_name}
        updated_data.update(data)

        # Write back to file with proper formatting
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(updated_data, f, ensure_ascii=False, indent=4)

        print(f"  ✓ Added language_name: {language_name}")

if __name__ == "__main__":
    add_language_names()
    print("\n✓ All translation files updated!")
