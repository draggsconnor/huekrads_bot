import pytest

import text_resources


def test_adventure_yaml_loads_utf8_text_with_html():
    text_resources._load_resources.cache_clear()

    text = text_resources.get_text(
        "adventure.expedition_start",
        username="Гном Вася",
        location_name="Охуенно тёмный лес",
        duration=5,
    )

    assert "Гном Вася" in text
    assert "Охуенно тёмный лес" in text
    assert "5" in text
    assert "<b>" in text


def test_text_resource_missing_key_and_placeholder_have_clear_errors():
    with pytest.raises(KeyError, match="Unknown text key"):
        text_resources.get_text("adventure.no.such.key")

    with pytest.raises(KeyError, match="Missing placeholder 'username'"):
        text_resources.get_text("adventure.expedition_start", location_name="лес", duration=5)
