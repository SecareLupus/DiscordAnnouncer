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


def test_finalize_payload_sets_defaults(sample_payload):
    result = finalize_payload(sample_payload, allowed_mentions=None, suppress_embeds=True)
    assert result["allowed_mentions"] == {"parse": []}
    assert result["flags"] & 4 == 4


def test_deliver_payload_success(httpserver: HTTPServer, tmp_path: Path, sample_payload):
    httpserver.expect_request("/api/webhooks/123/abc", method="POST").respond_with_data("ok", status=204)

    file_path = tmp_path / "banner.txt"
    file_path.write_text("file")

    attachment = Attachment(path=file_path, name=file_path.name, content_type="text/plain")

    payload = finalize_payload(sample_payload, allowed_mentions=None, suppress_embeds=False)
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
