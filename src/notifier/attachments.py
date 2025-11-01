from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


class AttachmentError(RuntimeError):
    """Raised when attachment validation fails."""


@dataclass(frozen=True)
class Attachment:
    path: Path
    name: str
    content_type: str


def _guess_type(path: Path) -> str:
    content_type, _ = mimetypes.guess_type(str(path))
    return content_type or "application/octet-stream"


def prepare_attachments(files: Iterable[str]) -> List[Attachment]:
    attachments: List[Attachment] = []

    for raw in files:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise AttachmentError(f"Attachment '{raw}' does not exist")
        if not path.is_file():
            raise AttachmentError(f"Attachment '{raw}' is not a file")

        attachments.append(
            Attachment(
                path=path,
                name=path.name,
                content_type=_guess_type(path),
            )
        )

    return attachments
