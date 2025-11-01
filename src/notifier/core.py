from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import requests

from .attachments import Attachment

logger = logging.getLogger(__name__)

REDACT_WEBHOOKS = True

DISCORD_MAX_CONTENT = 2000
DISCORD_MAX_EMBEDS = 10
DISCORD_MAX_TITLE = 256
DISCORD_MAX_DESCRIPTION = 4096
DISCORD_MAX_FOOTER = 2048
DISCORD_MAX_AUTHOR = 256


class PayloadValidationError(RuntimeError):
    """Raised when the rendered payload fails validation."""


class WebhookDeliveryError(RuntimeError):
    """Raised when posting to a webhook fails."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DeliveryResult:
    url: str
    status_code: int
    body: str = ""


def finalize_payload(
    payload: Mapping[str, object],
    *,
    allowed_mentions: Optional[Mapping[str, object]],
    suppress_embeds: bool,
) -> Dict[str, object]:
    result = deepcopy(dict(payload))

    if allowed_mentions is not None:
        result["allowed_mentions"] = allowed_mentions
    else:
        result.setdefault("allowed_mentions", {"parse": []})

    if suppress_embeds:
        flags = int(result.get("flags", 0))
        result["flags"] = flags | 4

    return result


def _ensure_embed_limits(embed: MutableMapping[str, object], *, index: int) -> None:
    def _check_text(key: str, value: object, limit: int) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            raise PayloadValidationError(f"embed[{index}].{key} must be a string")
        if len(value) > limit:
            raise PayloadValidationError(
                f"embed[{index}].{key} exceeds maximum length ({len(value)}/{limit})"
            )

    _check_text("title", embed.get("title"), DISCORD_MAX_TITLE)
    _check_text("description", embed.get("description"), DISCORD_MAX_DESCRIPTION)

    footer = embed.get("footer")
    if isinstance(footer, Mapping):
        _check_text("footer.text", footer.get("text"), DISCORD_MAX_FOOTER)

    author = embed.get("author")
    if isinstance(author, Mapping):
        _check_text("author.name", author.get("name"), DISCORD_MAX_AUTHOR)


def validate_payload(payload: Mapping[str, object], attachments: Sequence[Attachment]) -> None:
    content = payload.get("content")
    if content is not None:
        if not isinstance(content, str):
            raise PayloadValidationError("content must be a string")
        if len(content) > DISCORD_MAX_CONTENT:
            raise PayloadValidationError(
                f"content exceeds Discord limit ({len(content)}/{DISCORD_MAX_CONTENT})"
            )

    embeds = payload.get("embeds")
    if embeds is not None:
        if not isinstance(embeds, Sequence):
            raise PayloadValidationError("embeds must be a sequence")
        if len(embeds) > DISCORD_MAX_EMBEDS:
            raise PayloadValidationError("Discord allows at most 10 embeds")
        for idx, embed in enumerate(embeds):
            if not isinstance(embed, MutableMapping):
                raise PayloadValidationError(f"embed at index {idx} must be an object")
            _ensure_embed_limits(embed, index=idx)

    if attachments and len(attachments) > 10:
        raise PayloadValidationError("Discord allows at most 10 attachments")


def set_redaction(enabled: bool) -> None:
    global REDACT_WEBHOOKS
    REDACT_WEBHOOKS = enabled


def redact_webhook(url: str) -> str:
    if not REDACT_WEBHOOKS:
        return url
    if "/api/webhooks/" not in url:
        return url
    prefix, suffix = url.split("/api/webhooks/", 1)
    return f"{prefix}/api/webhooks/****redacted****"


def _prepare_request_payload(
    payload: Mapping[str, object],
    attachments: Sequence[Attachment],
) -> Tuple[Dict[str, object], Optional[List[Tuple[str, Tuple[str, object, str]]]]]:
    if not attachments:
        return dict(payload), None

    prepared = dict(payload)
    files: List[Tuple[str, Tuple[str, object, str]]] = []
    attachment_payload: List[Dict[str, object]] = []
    for index, attachment in enumerate(attachments):
        handle = attachment.path.open("rb")
        files.append((f"files[{index}]", (attachment.name, handle, attachment.content_type)))
        attachment_payload.append({"id": str(index), "filename": attachment.name})

    if attachment_payload:
        prepared["attachments"] = attachment_payload
    return prepared, files


def _close_files(files: Optional[List[Tuple[str, Tuple[str, object, str]]]]) -> None:
    if not files:
        return
    for _, (_, handle, _) in files:
        try:
            handle.close()
        except Exception:  # pragma: no cover - defensive clean-up
            logger.debug("Failed to close attachment handle", exc_info=True)


def _compute_retry_delay(response: requests.Response) -> float:
    retry_after = response.headers.get("Retry-After") or response.headers.get("X-RateLimit-Reset-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            logger.debug("Invalid Retry-After header: %s", retry_after)
    try:
        data = response.json()
    except ValueError:
        return 0.0
    retry_after = data.get("retry_after")
    if retry_after is None:
        return 0.0
    try:
        return float(retry_after)
    except (TypeError, ValueError):
        return 0.0


def post_to_webhook(
    *,
    session: requests.Session,
    webhook_url: str,
    payload: Mapping[str, object],
    attachments: Sequence[Attachment],
    retry: bool,
    thread_id: Optional[str],
    timeout: float = 10.0,
) -> DeliveryResult:
    prepared_payload, files = _prepare_request_payload(payload, attachments)
    data_kwargs: Dict[str, object]

    params: Dict[str, str] = {"wait": "true"}
    if thread_id:
        params["thread_id"] = thread_id

    try:
        if files:
            data_kwargs = {"data": {"payload_json": json.dumps(prepared_payload)}, "files": files}
        else:
            data_kwargs = {"json": prepared_payload}

        attempt = 0
        while True:
            response = session.post(
                webhook_url,
                timeout=timeout,
                params=params,
                **data_kwargs,
            )
            if response.status_code == 429 and retry and attempt == 0:
                delay = _compute_retry_delay(response)
                logger.warning(
                    "Rate limited by Discord webhook %s; retrying in %.2fs",
                    redact_webhook(webhook_url),
                    delay,
                )
                if delay > 0:
                    time.sleep(min(delay, 5.0))
                attempt += 1
                continue
            break
    finally:
        _close_files(files)

    if response.status_code >= 400:
        snippet = response.text[:1000]
        raise WebhookDeliveryError(
            f"Webhook POST failed ({response.status_code}) for {redact_webhook(webhook_url)}: {snippet}",
            status_code=response.status_code,
        )

    return DeliveryResult(url=webhook_url, status_code=response.status_code, body=response.text)


def deliver_payload(
    webhook_urls: Sequence[str],
    payload: Mapping[str, object],
    *,
    attachments: Sequence[Attachment],
    retry: bool = True,
    thread_id: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> List[DeliveryResult]:
    if not webhook_urls:
        raise WebhookDeliveryError("No webhook URL provided")

    owns_session = session is None
    session = session or requests.Session()

    results: List[DeliveryResult] = []
    try:
        for url in webhook_urls:
            logger.info("Sending webhook payload to %s", redact_webhook(url))
            result = post_to_webhook(
                session=session,
                webhook_url=url,
                payload=payload,
                attachments=attachments,
                retry=retry,
                thread_id=thread_id,
            )
            results.append(result)
    finally:
        if owns_session:
            session.close()

    return results
