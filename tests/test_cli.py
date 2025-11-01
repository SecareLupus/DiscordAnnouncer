from pathlib import Path

from src.notifier.cli import run_cli


def test_cli_dry_run(tmp_path, capsys):
    template = tmp_path / "payload.json.j2"
    template.write_text('{"content": "{{ message }}"}')

    env_file = tmp_path / "vars.env"
    env_file.write_text("DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/abc\n")

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
