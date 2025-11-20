from pathlib import Path

import pytest
from pytest_httpserver import HTTPServer

from src.notifier.attachments import Attachment
from src.notifier.core import (
    DeliveryResult,
    PayloadValidationError,
    deliver_payload,
    finalize_payload,
    validate_payload,
)


@pytest.fixture
def sample_payload():
    return {
        "content": "hello",
        "embeds": [
            {
                "title": "Sample",
                "description": "World",
            }
        ],
    }


def test_validate_payload_length(sample_payload):
    validate_payload(sample_payload, [])

    sample_payload["content"] = "x" * 2001
    with pytest.raises(PayloadValidationError):
        validate_payload(sample_payload, [])


def test_validate_payload_fields_guardrails(sample_payload):
    sample_payload["embeds"][0]["fields"] = [
        {"name": f"Field {i}", "value": "Value"} for i in range(26)
    ]
    with pytest.raises(PayloadValidationError):
        validate_payload(sample_payload, [])

    sample_payload["embeds"][0]["fields"] = [{"name": "Name only"}]
    with pytest.raises(PayloadValidationError):
        validate_payload(sample_payload, [])

    sample_payload["embeds"][0]["fields"] = [
        {"name": "Field", "value": "Value", "inline": "yes"}
    ]
    with pytest.raises(PayloadValidationError):
        validate_payload(sample_payload, [])


def test_validate_payload_timestamp_and_totals(sample_payload):
    sample_payload["embeds"][0]["timestamp"] = "not a timestamp"
    with pytest.raises(PayloadValidationError):
        validate_payload(sample_payload, [])

    # description 4096 + 10 fields (each name/value total 510) = 9196 characters
    sample_payload["embeds"][0]["timestamp"] = "2023-09-15T12:00:00Z"
    sample_payload["embeds"][0]["description"] = "d" * 4096
    sample_payload["embeds"][0]["fields"] = [
        {
            "name": f"Slot {i}",
            "value": "v" * 500,
        }
        for i in range(10)
    ]
    with pytest.raises(PayloadValidationError):
        validate_payload(sample_payload, [])


def test_validate_payload_rejects_invalid_embed_objects(sample_payload):
    sample_payload["embeds"][0]["footer"] = "not a footer"
    with pytest.raises(PayloadValidationError):
        validate_payload(sample_payload, [])


def test_filter_attachments_for_payload():
    payload = {
        "embeds": [
            {
                "image": {"url": "attachment://banner.png"},
                "thumbnail": {"url": "attachment://thumb.jpg"},
            }
        ]
    }
    a = Attachment(
        path=Path("banner.png"),
        name="banner.png",
        content_type="image/png",
        embed_only=True,
    )
    b = Attachment(
        path=Path("thumb.jpg"),
        name="thumb.jpg",
        content_type="image/jpeg",
        embed_only=True,
    )
    c = Attachment(
        path=Path("extra.png"),
        name="extra.png",
        content_type="image/png",
        embed_only=True,
    )
    uploads = Attachment(
        path=Path("document.pdf"),
        name="document.pdf",
        content_type="application/pdf",
        embed_only=False,
    )

    used, unused = filter_attachments_for_payload(payload, [a, b, c, uploads])

    assert [item.name for item in used] == ["banner.png", "thumb.jpg", "document.pdf"]
    assert [item.name for item in unused] == ["extra.png"]


def test_finalize_payload_sets_defaults(sample_payload):
    result = finalize_payload(
        sample_payload, allowed_mentions=None, suppress_embeds=True
    )
    assert result["allowed_mentions"] == {"parse": []}
    assert result["flags"] & 4 == 4


def test_deliver_payload_success(
    httpserver: HTTPServer, tmp_path: Path, sample_payload
):
    httpserver.expect_request("/api/webhooks/123/abc", method="POST").respond_with_data(
        "ok", status=204
    )

    file_path = tmp_path / "banner.txt"
    file_path.write_text("file")

    attachment = Attachment(
        path=file_path, name=file_path.name, content_type="text/plain"
    )

    payload = finalize_payload(
        sample_payload, allowed_mentions=None, suppress_embeds=False
    )
    validate_payload(payload, [attachment])

    result = deliver_payload(
        [httpserver.url_for("/api/webhooks/123/abc")],
        payload,
        attachments=[attachment],
        retry=False,
    )

    assert len(result) == 1
    assert isinstance(result[0], DeliveryResult)
    assert result[0].status_code == 204
