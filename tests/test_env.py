from pathlib import Path

import pytest

from src.notifier.env import load_environment, normalize_env_keys


def test_load_environment_precedence(tmp_path: Path, monkeypatch):
    base_env = tmp_path / ".env"
    base_env.write_text("FOO=base\nBAR=base\n")

    override_env = tmp_path / "override.env"
    override_env.write_text("BAR=override\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BAZ", "system")

    values = load_environment(override_env, search_paths=[Path(".env")])

    assert values["FOO"] == "base"
    assert values["BAR"] == "override"
    assert values["BAZ"] == "system"


@pytest.mark.parametrize(
    "key, expected",
    [
        ("BOT_USERNAME", {"BOT_USERNAME"}),
        ("bot-username", {"bot-username", "BOT-USERNAME", "BOT_USERNAME"}),
    ],
)
def test_normalize_env_keys(key, expected):
    result = normalize_env_keys({key: "value"})
    for variant in expected:
        assert result[variant] == "value"
