import json
from string import Formatter
from typing import Any

from src.config import LANG_PATH
from src.i18n.translator import GUISettings


def _assert_translation_shape(reference: Any, translation: Any, path: str = "root") -> None:
    assert type(translation) is type(reference), path

    if isinstance(reference, dict):
        assert set(translation) == set(reference), path
        for key, value in reference.items():
            _assert_translation_shape(value, translation[key], f"{path}.{key}")
    elif isinstance(reference, list):
        assert len(translation) == len(reference), path
        for index, value in enumerate(reference):
            _assert_translation_shape(value, translation[index], f"{path}[{index}]")
    elif isinstance(reference, str):
        reference_fields = sorted(
            (
                field_name,
                format_spec,
                conversion or "",
            )
            for _, field_name, format_spec, conversion in Formatter().parse(reference)
            if field_name is not None
        )
        translation_fields = sorted(
            (
                field_name,
                format_spec,
                conversion or "",
            )
            for _, field_name, format_spec, conversion in Formatter().parse(translation)
            if field_name is not None
        )
        assert translation_fields == reference_fields, path


def test_all_language_settings_include_gui_settings_schema_keys():
    required_settings_keys = set(GUISettings.__annotations__)
    english_settings = json.loads((LANG_PATH / "English.json").read_text(encoding="utf-8"))["gui"][
        "settings"
    ]
    missing_by_language = {}

    for filepath in LANG_PATH.glob("*.json"):
        translation = json.loads(filepath.read_text(encoding="utf-8"))
        settings = translation["gui"]["settings"]
        missing = sorted(required_settings_keys - set(settings))
        if missing:
            missing_by_language[filepath.name] = missing

    assert sorted(set(english_settings) - required_settings_keys) == []
    assert missing_by_language == {}


def test_all_translations_match_english_schema_and_placeholders():
    english = json.loads((LANG_PATH / "English.json").read_text(encoding="utf-8"))

    for filepath in sorted(LANG_PATH.glob("*.json")):
        translation = json.loads(filepath.read_text(encoding="utf-8"))
        _assert_translation_shape(english, translation, f"{filepath.name}:root")


def test_hungarian_translation_metadata():
    hungarian = json.loads((LANG_PATH / "Magyar.json").read_text(encoding="utf-8"))

    assert hungarian["language_name"] == "Magyar"
    assert hungarian["english_name"] == "Hungarian"
