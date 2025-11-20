from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, MutableMapping, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from jinja2 import TemplateError as JinjaTemplateError

from .env import normalize_env_keys

logger = logging.getLogger(__name__)


class TemplateRenderError(RuntimeError):
    """Raised when template rendering fails or produces invalid JSON."""


def _tojson(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def embed_footer(
    text: object,
    *,
    icon_url: Optional[str] = None,
    proxy_icon_url: Optional[str] = None,
) -> Dict[str, object]:
    footer: Dict[str, object] = {"text": str(text)}
    if icon_url:
        footer["icon_url"] = icon_url
    if proxy_icon_url:
        footer["proxy_icon_url"] = proxy_icon_url
    return footer


def embed_field(name: object, value: object, inline: bool = False) -> Dict[str, object]:
    return {
        "name": str(name),
        "value": str(value),
        "inline": bool(inline),
    }


def embed_timestamp(value: Optional[object] = None) -> str:
    if value is None:
        target = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        target = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        target = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        return value
    else:
        raise TemplateRenderError(
            "embed_timestamp requires a datetime, timestamp, or ISO string"
        )
    return target.astimezone(timezone.utc).isoformat()


def parse_var_assignments(items: Iterable[str]) -> Dict[str, str]:
    assignments: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise TemplateRenderError(
                f"Invalid --var assignment '{item}'. Use key=value."
            )
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise TemplateRenderError("Variable names must not be empty")
        assignments[key] = value
    return assignments


def _build_environment(template_path: Path) -> Environment:
    loader = FileSystemLoader(str(template_path.parent))
    env = Environment(
        loader=loader,
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.setdefault("tojson", _tojson)
    return env


def build_template_context(
    *,
    message: str,
    include_everyone: bool,
    env_values: Mapping[str, str],
    overrides: Mapping[str, str],
    extra_context: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    context: Dict[str, object] = {}

    normalized_env = normalize_env_keys(env_values)
    context.update(normalized_env)
    context.update(overrides)

    context["message"] = message
    context["message_prefix"] = "@everyone, " if include_everyone else ""
    context["now_iso"] = datetime.now(timezone.utc).isoformat()
    context["embed_footer"] = embed_footer
    context["embed_field"] = embed_field
    context["embed_timestamp"] = embed_timestamp

    if extra_context:
        context.update(extra_context)

    return context


def render_template(
    template_path: Path,
    context: Mapping[str, object],
) -> Dict[str, object]:
    try:
        env = _build_environment(template_path)
        template = env.get_template(template_path.name)
        rendered = template.render(context)
    except JinjaTemplateError as exc:
        logger.debug("Template rendering failed: %s", exc, exc_info=True)
        raise TemplateRenderError(f"Template rendering failed: {exc}") from exc

    try:
        payload = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise TemplateRenderError(
            f"Rendered template is not valid JSON: {exc.msg} (line {exc.lineno} column {exc.colno})"
        ) from exc

    if not isinstance(payload, MutableMapping):
        raise TemplateRenderError(
            "Template must render to a JSON object at the top level"
        )

    return payload
