from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Mapping, MutableMapping, Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError as JinjaTemplateError

from .env import normalize_env_keys

logger = logging.getLogger(__name__)


class TemplateRenderError(RuntimeError):
    """Raised when template rendering fails or produces invalid JSON."""


def parse_var_assignments(items: Iterable[str]) -> Dict[str, str]:
    assignments: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise TemplateRenderError(f"Invalid --var assignment '{item}'. Use key=value.")
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
        raise TemplateRenderError("Template must render to a JSON object at the top level")

    return payload
