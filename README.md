# Discord Webhook Notifier

Discord Webhook Notifier is a Python application for crafting Discord webhook messages with reusable templates. It ships with a command line interface and a Tk-based desktop helper; both produce identical payloads by sharing the same execution path.

## Why Use It?

- Keep announcement embeds consistent by rendering Jinja2 templates to Discord-compatible JSON.
- Merge configuration from `.env` files, environment variables, and CLI overrides without leaking secrets.
- Post messages with optional attachments, `@everyone` prefixes, and safe default mention settings.
- Handle Discord rate limits gracefully by retrying once with respect for the provided delay.

## Install & Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev]
```

Copy `.env.example` to `.env` and fill in at least `DISCORD_WEBHOOK_URL`. Optional values like `BOT_USERNAME`, `BOT_AVATAR_URL`, and `DEFAULT_COLOR` serve as template defaults.

## Command Line Reference

Basic invocation:

```bash
python3 -m src.main \
  --template templates/live_announcement.json.j2 \
  --message "Stream is live!" \
  --var stream_url="https://twitch.tv/FoxyLupi"
```

Helpful flags:

- `--file PATH` include a file upload (refer to it with `attachment://filename` in templates).
- `--everyone` prepend `@everyone, ` to the message body.
- `--allow-mentions everyone,roles` opt into additional mention types; defaults disallow mass pings.
- `--dry-run` render JSON to stdout without contacting Discord.
- `--no-retry` disable the automatic retry after a 429 response.
- `--webhook URL` send to a specific webhook; repeat for multiple URLs.

Exit codes: `0` success, `2` invalid input, `3` HTTP failure, `4` rate limit exhausted, `5` template error.

## Templates & Variables

Templates live in `templates/` and use the `.json.j2` suffix. Render-time variables come from, in order of precedence:

1. `.env` and process environment values.
2. CLI overrides such as `--webhook`.
3. `--var key=value` assignments.

The rendering context also exposes `message`, `message_prefix`, `now_iso` (UTC timestamp), and all normalized environment keys. Templates must produce a single JSON object; the application validates Discord field length limits before sending.

## Attachments

Pass `--file path/to/banner.png` to include uploads. The CLI builds the multipart form automatically and marks each file with its name and content type. Up to 10 attachments are supported, matching Discord’s limits.

## Tk GUI Companion

Launch the graphical frontend with:

```bash
python3 -m src.notifier.gui_tk
```

The window lets you pick a webhook, template, message, variables (one `key=value` per line), and attachments. “Preview” runs the CLI with `--dry-run` and shows the captured output; “Send” executes the real request. Recent inputs persist in `~/.discord-webhook-notifier/state.json`.

## Development

- `python3 -m src.main --help` lists the full CLI surface.
- `pytest` executes the unit test suite (install `.[dev]` extras first).
- Project metadata and dependencies are declared in `pyproject.toml`; the package exposes a `discord-webhook-notifier` console entry point when installed.

## License

GPL-3.0-or-later.
