# Project Description: **discord-webhook-notifier**

A small, cross-platform Python tool that posts **custom embeds** to a Discord channel via **webhooks**, usable both from a **CLI** and a lightweight **Tk GUI**. The GUI is a thin wrapper that builds the same CLI command and executes it, so behavior is identical in both modes.

---

## Goals & Capabilities

* Read **credentials & webhook URLs** from an **environment file** (e.g., `.env`).
* Send a **message** with optional **@everyone** prefix.
* Apply a user-selected **embed banner template** (with placeholders) and fill those placeholders with runtime values.
* Support **file attachments** referenced as `attachment://...` inside the embed template.
* Provide clear **validation**, **logging**, and **rate-limit** handling.

---

## High-Level Architecture

```
discord-webhook-notifier/
├─ src/
│  ├─ notifier/
│  │  ├─ __init__.py
│  │  ├─ cli.py                # argparse CLI
│  │  ├─ core.py               # payload build, template rendering, HTTP post
│  │  ├─ env.py                # .env loading & validation
│  │  ├─ templates.py          # schema + renderer (Jinja2)
│  │  ├─ attachments.py        # file upload handling
│  │  └─ gui_tk.py             # Tk frontend that shells out to CLI
│  └─ main.py                  # console_scripts entry point
├─ templates/
│  ├─ live_announcement.json.j2
│  └─ schedule_card.json.j2
├─ .env.example
├─ README.md
├─ pyproject.toml
├─ LICENSE
└─ tests/
   ├─ test_cli.py
   ├─ test_templates.py
   └─ test_core.py
```

---

## Environment & Configuration

### `.env` (example)

```
# Required
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123/abc
# Optional defaults
BOT_USERNAME=Live Alert Bot
BOT_AVATAR_URL=https://example.com/bot-icon.png
DEFAULT_COLOR=0x9B59B6
```

* Loaded via `python-dotenv`.
* CLI flags override `.env` defaults.
* Multiple webhooks are supported via `--webhook` flag; if omitted, uses `DISCORD_WEBHOOK_URL`.

---

## Template System

### Format

* **Jinja2** over **JSON** (or YAML) to keep things human-editable and allow placeholders.
* The rendered result must conform to Discord’s message payload schema: top-level fields like `content`, `embeds`, `username`, `avatar_url`, `allowed_mentions`, and optional `attachments`.

### Placeholders

* Any `{{ variable }}` is filled from:

  1. CLI `--var key=value` pairs,
  2. `.env` values,
  3. built-in context (timestamp, ISO8601, etc.).

### Example Template (`templates/live_announcement.json.j2`)

```json
{
  "username": "{{ BOT_USERNAME | default('Live Alert Bot') }}",
  "avatar_url": "{{ BOT_AVATAR_URL | default('') }}",
  "content": "{{ message_prefix | default('') }}{{ message }}",
  "embeds": [
    {
      "title": "{{ title | default('We’re LIVE!') }}",
      "url": "https://twitch.tv/{{ TWITCH_CHANNEL | default('YourChannel') }}",
      "description": "{{ description | default('Come hang out!') }}",
      "color": {{ color | default(DEFAULT_COLOR | int, true) }},
      "thumbnail": {"url": "{{ thumbnail_url | default('') }}"},
      "image": {"url": "{{ banner_url | default('') }}"},
      "footer": {"text": "{{ footer_text | default('Join us in chat!') }}"},
      "timestamp": "{{ now_iso }}"
    }
  ],
  "allowed_mentions": {"parse": []}
}
```

**Built-in context** provided by the script:

* `now_iso`: current UTC ISO8601 string.
* `message`: the raw message input.
* `message_prefix`: set to `"@everyone, "` if the boolean is true, else `""`.
* All `.env` variables (names normalized to identifiers).

### Optional Attachments

If your embed references `attachment://banner.png`, pass `--file banner.png`. The tool sends a multipart request and the embed’s `image.url` resolves to the attached file.

---

## CLI

### Usage

```
discord-webhook-notifier \
  --template ./templates/live_announcement.json.j2 \
  --message "Stream is up: spooky speedruns!" \
  --everyone false \
  --var title="Halloween Marathon" \
  --var TWITCH_CHANNEL="FoxyLupi" \
  --var banner_url="https://static-cdn.jtvnw.net/previews-ttv/live_user_FoxyLupi-1280x720.jpg" \
  --file ./art/banner.png \
  --webhook $DISCORD_WEBHOOK_URL
```

### Flags

* `--template PATH` (required): Jinja2 template file.
* `--message TEXT` (required): Message content (pre-@everyone handled automatically).
* `--everyone BOOL` (default `false`): If true, prefix content with `"@everyone, "`.
* `--var key=value` (repeatable): Arbitrary variables injected into the template context.
* `--file PATH` (repeatable): Attach files; referenced as `attachment://filename` within embeds.
* `--webhook URL` (optional): Override env URL.
* `--username TEXT`, `--avatar-url URL` (optional): Override `.env`.
* `--dry-run`: Render & print final JSON (and attachment list) but do not send.
* `--timeout SECONDS` (default 10)
* `--suppress-previews` (wrap bare links in `< >` in `content`, if desired)
* `-v/--verbose` for debug logs, `-q/--quiet` to minimize output.
* Exit codes: `0` success, `2` validation error, `3` HTTP error, `4` rate-limit exhausted, `5` template error.

---

## Tk GUI Frontend

**Goal:** A minimal GUI that mirrors the CLI.

**Features:**

* Fields: Webhook URL (with dropdown for saved ones), Message (multiline), `@everyone` checkbox, Template file picker, Key/Value table for variables, File attachments list, Preview pane (shows rendered JSON), Send button.
* **Behavior:** On “Send”, the GUI constructs the CLI command and spawns it (showing stdout/stderr in a log area). On “Preview”, runs `--dry-run` and shows the rendered payload.
* **Persistence:** Remembers last used template, variables, and attachments (stored in a small `~/.discord-webhook-notifier/state.json`).

**Why shell out to CLI?**

* Guarantees identical logic between GUI & CLI.
* Keeps the GUI thin and testable.

---

## HTTP & Rate Limits

* Uses `requests` with:

  * JSON post if no attachments;
  * `multipart/form-data` if attachments present (`payload_json` + `files[n]`).
* If **429** received:

  * Read `Retry-After`/`X-RateLimit-Reset-After`.
  * Respect a single automatic retry unless `--no-retry`.
* Validate Discord limits:

  * Max 10 embeds; text limits enforced (title 256, desc 4096, etc.).
  * If over limits, fail fast with clear error.

---

## Validation & Safety

* **Template schema check**: after rendering, ensure payload keys align with Discord’s message schema.
* **Allowed mentions** default to `{"parse":[]}` (no accidental mass pings).
* Optional `--allow-mentions everyone,roles` to override.
* Sanitizes `--var` collisions (template variables take precedence over defaults but are logged).

---

## Logging

* Human-readable logs to stderr; structured JSON logs with `--verbose-json`.
* Obfuscates webhook URL in logs unless `--no-redact`.
* Optional rotating file log at `~/.discord-webhook-notifier/logs/`.

---

## Packaging & Installation

* **pyproject.toml** using `hatchling` or `setuptools`.
* `console_scripts` entry point: `discord-webhook-notifier = src.main:run`.
* `pipx install .` for isolated CLI.
* Minimum Python 3.9.

**Dependencies**

* `python-dotenv`, `jinja2`, `requests`, `click` or `argparse` (std), `tkinter` (bundled with most Python builds), `pydantic` (optional) for strict schema validation.

---

## Testing

* Unit tests for:

  * `.env` load precedence.
  * Template rendering (vars, defaults, timestamps).
  * Validation on length limits.
  * Attachment posting path.
  * Rate-limit retry.
* Integration tests: spin a local HTTP server to assert payload.

---

## Security

* Never echo full webhook URL in logs.
* `.env` excluded in `.gitignore`.
* Optional `--env PATH` to target a specific environment file without exposing secrets in shell history (recommend using a process manager or `.env` next to the binary in production).

---

## Example Workflows

### Live Announcement

```
discord-webhook-notifier \
  --template templates/live_announcement.json.j2 \
  --message "We’re live with co-op chaos!" \
  --everyone true \
  --var title="Friday Night Party" \
  --var TWITCH_CHANNEL="FoxyLupi" \
  --var banner_url="https://static-cdn.jtvnw.net/previews-ttv/live_user_FoxyLupi-1280x720.jpg"
```

### Schedule Card + Attachment

```
discord-webhook-notifier \
  --template templates/schedule_card.json.j2 \
  --message "This week’s schedule:" \
  --file ./assets/schedule.png \
  --var image_url="attachment://schedule.png"
```

---

## Extensibility

* Add `--thread-id` to post into a specific thread (`?thread_id=...`).
* Add `--suppress-embeds` to set `flags` bit in supported contexts (for non-embed messages).
* Add templated **button components** via webhook-compatible interaction payloads (if/when desired).
* Provide a **template gallery** folder for common scenarios (go live, VOD drop, poll, schedule).

---

## License

* GPLv3 by default, adjust as needed.
