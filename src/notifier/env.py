from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Mapping, MutableMapping, Optional

from dotenv import dotenv_values


class EnvironmentError(RuntimeError):
    """Raised when the environment configuration is invalid."""


def _load_dotenv(path: Path) -> Dict[str, str]:
    if not path.exists():
        raise EnvironmentError(f"Environment file {path} does not exist")
    values = dotenv_values(str(path))
    return {k: v for k, v in values.items() if v is not None}


def load_environment(
    env_path: Optional[Path] = None,
    *,
    search_paths: Iterable[Path] = (Path(".env"),),
) -> Dict[str, str]:
    """
    Load environment values from an optional .env file and the process env.

    Precedence (lowest to highest):
      1. .env files found in *search_paths* (first existing file wins)
      2. values from *env_path* if provided
      3. process environment variables

    Returns a dict with string values only.
    """

    merged: Dict[str, str] = {}

    for candidate in search_paths:
        candidate_path = Path(candidate)
        if not candidate_path.is_absolute():
            candidate_path = Path.cwd() / candidate_path
        if candidate_path.exists():
            merged.update(_load_dotenv(candidate_path))
            break

    if env_path:
        target = env_path if env_path.is_absolute() else Path.cwd() / env_path
        merged.update(_load_dotenv(target))

    for key, value in os.environ.items():
        if isinstance(value, str):
            merged[key] = value

    return merged


def apply_overrides(
    env: MutableMapping[str, str],
    overrides: Mapping[str, Optional[str]],
) -> Dict[str, str]:
    """Return a new dict with *overrides* applied on top of *env*."""
    merged = dict(env)
    for key, value in overrides.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def normalize_env_keys(env: Mapping[str, str]) -> Dict[str, str]:
    """
    Produce a mapping of normalized keys suitable for template rendering.

    Keys are converted to upper-case snake-case identifiers. Values remain
    untouched. Original keys are preserved as-is as well so both forms are
    accessible within templates.
    """
    normalized: Dict[str, str] = {}
    for key, value in env.items():
        normalized[key] = value
        upper = key.upper()
        normalized[upper] = value
        sanitized = upper.replace("-", "_")
        normalized[sanitized] = value
    return normalized
