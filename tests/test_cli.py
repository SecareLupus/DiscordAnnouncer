from pathlib import Path

import pytest

from src.notifier import cli
from src.notifier.cli import run_cli


def test_cli_dry_run(tmp_path, capsys):
    template = tmp_path / "payload.json.j2"
    template.write_text('{"content": "{{ message }}"}')

    env_file = tmp_path / "vars.env"
    env_file.write_text(
        "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/abc\n"
    )

    exit_code = run_cli(
        [
            "--template",
            str(template),
            "--message",
            "Hello",
            "--env",
            str(env_file),
            "--dry-run",
        ]
    )

    stdout, stderr = capsys.readouterr()
    assert exit_code == 0
    assert "Hello" in stdout
    assert stderr == ""


def test_cli_json_var_support(tmp_path, capsys):
    template = tmp_path / "payload.json.j2"
    template.write_text('{"count": {{ slots | length }}}')

    env_file = tmp_path / "vars.env"
    env_file.write_text(
        "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/abc\n"
    )

    exit_code = run_cli(
        [
            "--template",
            str(template),
            "--env",
            str(env_file),
            "--json-var",
            'slots=[{"name": "One"}, {"name": "Two"}]',
            "--dry-run",
        ]
    )

    stdout, stderr = capsys.readouterr()
    assert exit_code == 0
    assert '"count": 2' in stdout
    assert stderr == ""


def test_normalize_twitch_channel_variants():
    assert cli._normalize_twitch_channel("https://twitch.tv/Streamer/") == "Streamer"
    assert cli._normalize_twitch_channel("@streamer") == "streamer"
    assert cli._normalize_twitch_channel("streamer") == "streamer"


def test_resolve_live_channel_prefers_cli_vars():
    env_values = {"TWITCH_CHANNEL": "env"}
    cli_vars = {"TWITCH_CHANNEL": "cli"}
    assert cli._resolve_live_channel(cli_vars, env_values) == "cli"


def test_resolve_live_channel_requires_value():
    with pytest.raises(ValueError):
        cli._resolve_live_channel({}, {})


def test_wait_for_live_succeeds_without_sleeping():
    calls = []

    def fake_check(channel: str, session=None) -> bool:
        calls.append(channel)
        return len(calls) > 1

    sleep_durations = []

    def fake_sleep(duration: float) -> None:
        sleep_durations.append(duration)

    def time_gen(step: float = 0.5):
        current = 0.0

        def _tick() -> float:
            nonlocal current
            current += step
            return current

        return _tick

    time_fn = time_gen()
    result = cli._wait_for_live(
        "channel",
        interval=1.0,
        timeout=5.0,
        check_fn=fake_check,
        sleep_fn=fake_sleep,
        time_fn=time_fn,
    )

    assert result is True
    assert sleep_durations == [1.0]
    assert calls == ["channel", "channel"]


def test_wait_for_live_times_out():
    def always_offline(channel: str, session=None) -> bool:
        return False

    sleep_calls = []

    def fake_sleep(duration: float) -> None:
        sleep_calls.append(duration)

    def time_gen(step: float = 2.0):
        current = 0.0

        def _tick() -> float:
            nonlocal current
            current += step
            return current

        return _tick

    time_fn = time_gen()
    result = cli._wait_for_live(
        "channel",
        interval=1.0,
        timeout=3.0,
        check_fn=always_offline,
        sleep_fn=fake_sleep,
        time_fn=time_fn,
    )

    assert result is False
    assert sleep_calls  # ensures we attempted to wait


def test_wait_for_live_missing_channel_returns_validation(tmp_path, capsys):
    template = tmp_path / "payload.json.j2"
    template.write_text("{}")

    env_file = tmp_path / "vars.env"
    env_file.write_text(
        "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/abc\n"
    )

    exit_code = run_cli(
        [
            "--template",
            str(template),
            "--env",
            str(env_file),
            "--wait-for-live",
        ]
    )

    assert exit_code == cli.EXIT_VALIDATION
