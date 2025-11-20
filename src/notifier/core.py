from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import (
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
)

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
DISCORD_MAX_EMBED_TOTAL = 6000
DISCORD_MAX_FIELDS = 25
DISCORD_MAX_FIELD_NAME = 256
DISCORD_MAX_FIELD_VALUE = 1024


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
    total_characters = 0

    def _check_text(key: str, value: object, limit: int) -> None:
        nonlocal total_characters

        if value is None:
            return
        if not isinstance(value, str):
            raise PayloadValidationError(f"embed[{index}].{key} must be a string")
        if len(value) > limit:
            raise PayloadValidationError(
                f"embed[{index}].{key} exceeds maximum length ({len(value)}/{limit})"
            )
        total_characters += len(value)

    def _ensure_mapping(key: str) -> Optional[Mapping[str, object]]:
        value = embed.get(key)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise PayloadValidationError(f"embed[{index}].{key} must be an object")
        return value

    def _validate_timestamp(raw: object) -> None:
        if raw is None:
            return
        if not isinstance(raw, str):
            raise PayloadValidationError(f"embed[{index}].timestamp must be a string")
        candidate = raw
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            datetime.fromisoformat(candidate)
        except ValueError:
            raise PayloadValidationError(
                f"embed[{index}].timestamp must be ISO 8601 formatted"
            ) from None

    _check_text("title", embed.get("title"), DISCORD_MAX_TITLE)
    _check_text("description", embed.get("description"), DISCORD_MAX_DESCRIPTION)

    footer = _ensure_mapping("footer")
    if footer is not None:
        _check_text("footer.text", footer.get("text"), DISCORD_MAX_FOOTER)

    author = _ensure_mapping("author")
    if author is not None:
        _check_text("author.name", author.get("name"), DISCORD_MAX_AUTHOR)

    for media_key in ("thumbnail", "image", "video", "provider"):
        _ensure_mapping(media_key)

    fields = embed.get("fields")
    if fields is not None:
        if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
            raise PayloadValidationError(
                f"embed[{index}].fields must be a sequence of field objects"
            )
        if len(fields) > DISCORD_MAX_FIELDS:
            raise PayloadValidationError(
                f"embed[{index}].fields exceeds Discord limit ({len(fields)}/{DISCORD_MAX_FIELDS})"
            )
        for field_index, field in enumerate(fields):
            if not isinstance(field, Mapping):
                raise PayloadValidationError(
                    f"embed[{index}].fields[{field_index}] must be an object"
                )
            name = field.get("name")
            value = field.get("value")
            if name is None:
                raise PayloadValidationError(
                    f"embed[{index}].fields[{field_index}].name is required"
                )
            if value is None:
                raise PayloadValidationError(
                    f"embed[{index}].fields[{field_index}].value is required"
                )
            _check_text(f"fields[{field_index}].name", name, DISCORD_MAX_FIELD_NAME)
            _check_text(f"fields[{field_index}].value", value, DISCORD_MAX_FIELD_VALUE)
            inline = field.get("inline")
            if inline is not None and not isinstance(inline, bool):
                raise PayloadValidationError(
                    f"embed[{index}].fields[{field_index}].inline must be a boolean"
                )

    _validate_timestamp(embed.get("timestamp"))

    if total_characters > DISCORD_MAX_EMBED_TOTAL:
        raise PayloadValidationError(
            f"embed[{index}] exceeds the 6000 character aggregate limit ({total_characters}/{DISCORD_MAX_EMBED_TOTAL})"
        )


def _collect_attachment_references(
    value: object, *, results: Optional[set[str]] = None
) -> set[str]:
    if results is None:
        results = set()

    if isinstance(value, str):
        marker = "attachment://"
        if marker in value:
            _, _, suffix = value.partition(marker)
            if suffix:
                results.add(suffix)
        return results

    if isinstance(value, Mapping):
        for item in value.values():
            _collect_attachment_references(item, results=results)
        return results

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _collect_attachment_references(item, results=results)
    return results


def filter_attachments_for_payload(
    payload: Mapping[str, object], attachments: Sequence[Attachment]
) -> Tuple[List[Attachment], List[Attachment]]:
    """
    Partition attachments into used/unreferenced lists based on embed references.

    Attachments marked as *embed_only* must appear as attachment:// references
    within the payload. Attachments without that flag are always retained.
    """

    references = _collect_attachment_references(payload)
    used: List[Attachment] = []
    unused: List[Attachment] = []

    for attachment in attachments:
        if not attachment.embed_only:
            used.append(attachment)
            continue
        if attachment.name in references:
            used.append(attachment)
        else:
            unused.append(attachment)
    return used, unused


def validate_payload(
    payload: Mapping[str, object], attachments: Sequence[Attachment]
) -> None:
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
        files.append(
            (f"files[{index}]", (attachment.name, handle, attachment.content_type))
        )
        attachment_meta: Dict[str, object] = {
            "id": str(index),
            "filename": attachment.name,
        }
        if attachment.description:
            attachment_meta["description"] = attachment.description
        if attachment.explicit_content_type:
            attachment_meta["content_type"] = attachment.content_type
        attachment_payload.append(attachment_meta)

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
    retry_after = response.headers.get("Retry-After") or response.headers.get(
        "X-RateLimit-Reset-After"
    )
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
            data_kwargs = {
                "data": {"payload_json": json.dumps(prepared_payload)},
                "files": files,
            }
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

    return DeliveryResult(
        url=webhook_url, status_code=response.status_code, body=response.text
    )


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
