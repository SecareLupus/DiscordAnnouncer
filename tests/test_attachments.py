from pathlib import Path

import pytest

from src.notifier.attachments import AttachmentError, prepare_attachments


def test_prepare_attachments_supports_description_and_content_type(tmp_path: Path):
    file_path = tmp_path / "image.png"
    file_path.write_text("data")

    attachments = prepare_attachments(
        [f"{file_path}::Alt banner text::image/custom"], embed_only=True
    )

    assert len(attachments) == 1
    attachment = attachments[0]
    assert attachment.description == "Alt banner text"
    assert attachment.content_type == "image/custom"
    assert attachment.explicit_content_type is True
    assert attachment.embed_only is True


def test_prepare_attachments_missing_file(tmp_path: Path):
    with pytest.raises(AttachmentError):
        prepare_attachments([str(tmp_path / "missing.png")], embed_only=False)
