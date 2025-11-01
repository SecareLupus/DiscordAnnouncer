from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .attachments import AttachmentError, prepare_attachments
from .core import (
    DeliveryResult,
    PayloadValidationError,
    WebhookDeliveryError,
    deliver_payload,
    finalize_payload,
    set_redaction,
    validate_payload,
)
from .env import apply_overrides, load_environment
from .templates import TemplateRenderError, build_template_context, parse_var_assignments, render_template

EXIT_SUCCESS = 0
EXIT_VALIDATION = 2
EXIT_HTTP_ERROR = 3
EXIT_RATE_LIMIT = 4
EXIT_TEMPLATE_ERROR = 5

LOG = logging.getLogger(__name__)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - formatting logic
        payload = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _configure_logging(args: argparse.Namespace) -> None:
    level = logging.INFO
    if args.quiet:
        level = logging.WARNING
    if args.verbose:
        level = logging.DEBUG

    handler = logging.StreamHandler()
    if args.verbose_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        formatter = logging.Formatter("%(levelname)s - %(message)s")
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)


def _parse_allowed_mentions(raw: Optional[str]):
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return {"parse": []}

    parts = [part.strip() for part in raw.split(",") if part.strip()]
    valid = {"everyone", "roles", "users"}
    invalid = [p for p in parts if p not in valid]
    if invalid:
        raise ValueError(f"Invalid allowed mention values: {', '.join(invalid)}")
    return {"parse": parts}


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="discord-webhook-notifier",
        description="Send templated Discord webhook messages.",
    )
    parser.add_argument("--template", required=True, help="Path to the Jinja2 template file.")
    parser.add_argument("--message", default="", help="Message content sent alongside embeds.")
    parser.add_argument("--everyone", action="store_true", help="Prefix message with @everyone.")
    parser.add_argument("--var", action="append", default=[], help="Template variable assignment (key=value).")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        dest="files",
        help="Attachment file to upload (repeatable).",
    )
    parser.add_argument(
        "--webhook",
        action="append",
        default=[],
        help="Target webhook URL (repeatable). Defaults to DISCORD_WEBHOOK_URL.",
    )
    parser.add_argument("--env", help="Path to a .env file with defaults.")
    parser.add_argument("--dry-run", action="store_true", help="Render template and print payload without sending.")
    parser.add_argument("--no-retry", action="store_true", help="Disable automatic retry on rate limits.")
    parser.add_argument("--thread-id", help="Post message into a thread (thread_id query parameter).")
    parser.add_argument("--suppress-embeds", action="store_true", help="Set the suppress embeds flag on the message.")

    parser.add_argument("--allow-mentions", help="Comma-separated allowed mention types (everyone,roles,users).")

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    verbosity.add_argument("-q", "--quiet", action="store_true", help="Reduce output.")

    parser.add_argument("--verbose-json", action="store_true", help="Emit structured JSON logs.")
    parser.add_argument("--no-redact", action="store_true", help="Do not redact webhook URLs in logs.")

    parser.add_argument(
        "--version",
        action="version",
        version=_resolve_version(),
    )

    return parser.parse_args(argv)


def _resolve_version() -> str:
    try:
        from importlib.metadata import version
    except ImportError:  # pragma: no cover - Python <3.8 fallback
        from importlib_metadata import version  # type: ignore

    try:
        return version("discord-webhook-notifier")
    except Exception:
        return "0.0.0"


def _resolve_webhooks(args: argparse.Namespace, env_values: dict) -> List[str]:
    if args.webhook:
        return args.webhook

    default = env_values.get("DISCORD_WEBHOOK_URL")
    if default:
        return [default]

    raise PayloadValidationError("Webhook URL not provided. Use --webhook or set DISCORD_WEBHOOK_URL in the environment.")


def run_cli(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = _parse_args(argv)
    except Exception as exc:  # pragma: no cover - argparse handles usage
        print(str(exc), file=sys.stderr)
        return EXIT_VALIDATION

    _configure_logging(args)
    set_redaction(not args.no_redact)

    template_path = Path(args.template).expanduser()
    if not template_path.exists():
        LOG.error("Template %s does not exist", template_path)
        return EXIT_VALIDATION

    env_path = Path(args.env).expanduser() if args.env else None
    try:
        env_values = load_environment(env_path)
    except Exception as exc:
        LOG.error("Failed to load environment: %s", exc)
        return EXIT_VALIDATION

    try:
        cli_vars = parse_var_assignments(args.var)
    except TemplateRenderError as exc:
        LOG.error(str(exc))
        return EXIT_TEMPLATE_ERROR

    env_overrides = {}
    if args.webhook:
        env_overrides["DISCORD_WEBHOOK_URL"] = args.webhook[0]
    env_with_cli = apply_overrides(env_values, env_overrides)

    collisions = sorted(key for key in cli_vars if key in env_with_cli)
    if collisions:
        LOG.debug("Template variables override environment defaults for: %s", ", ".join(collisions))

    context = build_template_context(
        message=args.message,
        include_everyone=args.everyone,
        env_values=env_with_cli,
        overrides=cli_vars,
        extra_context={"has_attachments": bool(args.files)},
    )

    try:
        payload = render_template(template_path, context)
    except TemplateRenderError as exc:
        LOG.error(str(exc))
        return EXIT_TEMPLATE_ERROR

    try:
        allowed_mentions = _parse_allowed_mentions(args.allow_mentions)
    except ValueError as exc:
        LOG.error(str(exc))
        return EXIT_VALIDATION

    payload = finalize_payload(
        payload,
        allowed_mentions=allowed_mentions,
        suppress_embeds=args.suppress_embeds,
    )

    try:
        attachments = prepare_attachments(args.files)
    except AttachmentError as exc:
        LOG.error(str(exc))
        return EXIT_VALIDATION

    try:
        validate_payload(payload, attachments)
    except PayloadValidationError as exc:
        LOG.error(str(exc))
        return EXIT_VALIDATION

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return EXIT_SUCCESS

    try:
        webhooks = _resolve_webhooks(args, env_with_cli)
    except PayloadValidationError as exc:
        LOG.error(str(exc))
        return EXIT_VALIDATION

    retry = not args.no_retry
    try:
        results: List[DeliveryResult] = deliver_payload(
            webhooks,
            payload,
            attachments=attachments,
            retry=retry,
            thread_id=args.thread_id,
        )
    except WebhookDeliveryError as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            LOG.error("Discord rate-limit exhausted: %s", exc)
            return EXIT_RATE_LIMIT
        LOG.error("Failed to send webhook: %s", exc)
        return EXIT_HTTP_ERROR

    for result in results:
        LOG.info("Webhook delivered (%s)", result.status_code)

    return EXIT_SUCCESS


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run_cli(argv)
