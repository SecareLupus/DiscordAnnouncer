from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


class AttachmentError(RuntimeError):
    """Raised when attachment validation fails."""


@dataclass(frozen=True)
class Attachment:
    path: Path
    name: str
    content_type: str
    description: Optional[str] = None
    explicit_content_type: bool = False
    embed_only: bool = False


def _guess_type(path: Path) -> str:
    content_type, _ = mimetypes.guess_type(str(path))
    return content_type or "application/octet-stream"


def _parse_file_spec(raw: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Split attachment specs of the form PATH[::DESCRIPTION][::CONTENT_TYPE].

    DESCRIPTION or CONTENT_TYPE can be left empty to skip over the slot.
    """

    path_part = raw
    description = None
    content_type = None

    if "::" in raw:
        parts = raw.split("::", 2)
        path_part = parts[0]
        if len(parts) >= 2 and parts[1]:
            description = parts[1]
        if len(parts) == 3 and parts[2]:
            content_type = parts[2]

    return path_part, description, content_type


def prepare_attachments(files: Iterable[str], *, embed_only: bool) -> List[Attachment]:
    attachments: List[Attachment] = []

    for raw in files:
        path_part, description, override_content_type = _parse_file_spec(raw)
        path = Path(path_part).expanduser().resolve()
        if not path.exists():
            raise AttachmentError(f"Attachment '{path_part}' does not exist")
        if not path.is_file():
            raise AttachmentError(f"Attachment '{path_part}' is not a file")

        content_type = override_content_type or _guess_type(path)
        attachments.append(
            Attachment(
                path=path,
                name=path.name,
                content_type=content_type,
                description=description,
                explicit_content_type=bool(override_content_type),
                embed_only=embed_only,
            )
        )

    return attachments
