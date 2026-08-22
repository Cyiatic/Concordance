from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
import sysconfig
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
_MISSING = object()
_TIMESTAMP_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)
_ALLOWED_REMOTE_MEDIA_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
_MAX_REMOTE_MEDIA_BYTES = 25 * 1024 * 1024


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate[-1:].lower() == "z":
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalise_timestamp(value: Any) -> str | None:
    parsed = parse_timestamp(value)
    return parsed.isoformat().replace("+00:00", "Z") if parsed else None


def _has_timestamp_timezone(value: Any) -> bool:
    return isinstance(value, str) and bool(_TIMESTAMP_OFFSET.search(value.strip()))


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _remote_media_allowed(value: Any) -> bool:
    if not _is_http_url(value):
        return False
    hostname = (urlparse(str(value).strip()).hostname or "").lower().rstrip(".")
    return hostname in _ALLOWED_REMOTE_MEDIA_HOSTS


def _canonical_remote_reference(value: Any) -> Any:
    if not _remote_media_allowed(value):
        return value
    parsed = urlparse(str(value).strip())
    return parsed._replace(query="", fragment="").geturl()


def _safe_media_filename(value: Any, fallback: str) -> str:
    candidate = Path(urlparse(str(value).strip()).path).name if value else ""
    candidate = candidate or fallback
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return candidate or fallback


def _download_remote_media(url: str, destination: Path, max_bytes: int = _MAX_REMOTE_MEDIA_BYTES) -> int:
    if not _remote_media_allowed(url):
        raise ValueError(
            "Remote media download is limited to cdn.discordapp.com and media.discordapp.net"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    request = Request(url, headers={"User-Agent": "Concordance/1.0"})
    total = 0
    try:
        with urlopen(request, timeout=30) as response, temporary.open("wb") as handle:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError(f"Remote media exceeds the {max_bytes // (1024 * 1024)} MB safety limit")
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Remote media exceeds the {max_bytes // (1024 * 1024)} MB safety limit")
                handle.write(chunk)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return total


def _normalise_local_reference(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().replace("\\", "/")
    if _is_http_url(candidate) or candidate.lower().startswith("data:"):
        return None
    if candidate.startswith("/") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", candidate):
        return None
    parts = [part for part in candidate.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _validate_timestamp(value: Any, field: str, errors: list[str]) -> None:
    if parse_timestamp(value) is None or not _has_timestamp_timezone(value):
        errors.append(f"{field} must be an ISO-8601 timestamp with a timezone")


def _validate_local_reference(value: Any, field: str, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a relative local asset path or null")
    elif _normalise_local_reference(value) is None:
        errors.append(f"{field} must be a safe relative local asset path")


def _validate_attachment(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    if not isinstance(value.get("name"), str) or not value["name"].strip():
        errors.append(f"{field}.name must be a non-empty string")
    for key in ("path", "local_path"):
        if key in value:
            _validate_local_reference(value[key], f"{field}.{key}", errors)
    if "url" in value and value["url"] is not None and not _is_http_url(value["url"]):
        errors.append(f"{field}.url must be an HTTP(S) URL or null")
    if "mime" in value and value["mime"] is not None and not isinstance(value["mime"], str):
        errors.append(f"{field}.mime must be a string or null")
    if "size_bytes" in value and value["size_bytes"] is not None:
        size = value["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            errors.append(f"{field}.size_bytes must be a non-negative integer or null")


def _validate_reaction(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    if not isinstance(value.get("emoji"), str) or not value["emoji"].strip():
        errors.append(f"{field}.emoji must be a non-empty string")
    if "count" in value:
        count = value["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            errors.append(f"{field}.count must be a non-negative integer")
    if "me" in value and not isinstance(value["me"], bool):
        errors.append(f"{field}.me must be boolean")


def _validate_embed(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    for key in ("title", "description", "footer"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")
    if "url" in value and value["url"] is not None and not _is_http_url(value["url"]):
        errors.append(f"{field}.url must be an HTTP(S) URL or null")
    if "image_url" in value and value["image_url"] is not None and not _is_http_url(value["image_url"]):
        errors.append(f"{field}.image_url must be an HTTP(S) URL or null")
    for key in ("image_path", "local_path"):
        if key in value:
            _validate_local_reference(value[key], f"{field}.{key}", errors)


def _validate_provenance(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    for key in ("source_file", "source_id", "source_link"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")
    if isinstance(value.get("source_file"), str) and _normalise_local_reference(value["source_file"]) is None:
        errors.append(f"{field}.source_file must be a safe relative source path")
    if isinstance(value.get("source_link"), str) and not _is_http_url(value["source_link"]):
        errors.append(f"{field}.source_link must be an HTTP(S) URL")
    if "record_index" in value:
        index = value["record_index"]
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            errors.append(f"{field}.record_index must be a non-negative integer")


def _validate_source_display(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    for key in ("label", "date", "time"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")


def validate_archive(archive: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(archive, dict):
        return ["archive must be a JSON object"]

    if archive.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION}; got {archive.get('schema_version')!r}"
        )

    metadata = archive.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object")
    else:
        for field in ("kind", "title"):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                errors.append(f"metadata.{field} must be a non-empty string")
        if "channel_id" in metadata and metadata["channel_id"] is not None and not isinstance(metadata["channel_id"], str):
            errors.append("metadata.channel_id must be a string or null")
        if "captured_at" in metadata and metadata["captured_at"] is not None:
            _validate_timestamp(metadata["captured_at"], "metadata.captured_at", errors)
        if "display_timezone" in metadata and (
            not isinstance(metadata["display_timezone"], str) or not metadata["display_timezone"].strip()
        ):
            errors.append("metadata.display_timezone must be a non-empty string")
        for field in ("capture_range", "coverage"):
            if field in metadata and metadata[field] is not None and not isinstance(metadata[field], dict):
                errors.append(f"metadata.{field} must be an object or null")
        source = metadata.get("source")
        if source is not None:
            if not isinstance(source, dict):
                errors.append("metadata.source must be an object")
            else:
                for key in ("type", "label", "source_name"):
                    if key in source and source[key] is not None and not isinstance(source[key], str):
                        errors.append(f"metadata.source.{key} must be a string or null")
                if "notes" in source:
                    if not isinstance(source["notes"], list) or any(not isinstance(note, str) for note in source["notes"]):
                        errors.append("metadata.source.notes must be an array of strings")
                summary = source.get("import_summary")
                if summary is not None:
                    if not isinstance(summary, dict):
                        errors.append("metadata.source.import_summary must be an object")
                    else:
                        for field in (
                            "files_scanned",
                            "files_with_records",
                            "records_seen",
                            "records_imported",
                            "records_skipped",
                        ):
                            value = summary.get(field)
                            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                                errors.append(
                                    f"metadata.source.import_summary.{field} must be a non-negative integer"
                                )
                        reasons = summary.get("skipped_record_reasons")
                        if not isinstance(reasons, dict) or any(
                            not isinstance(key, str)
                            or isinstance(value, bool)
                            or not isinstance(value, int)
                            or value < 0
                            for key, value in (reasons.items() if isinstance(reasons, dict) else [])
                        ):
                            errors.append(
                                "metadata.source.import_summary.skipped_record_reasons must map strings to non-negative integers"
                            )
                        unreadable = summary.get("unreadable_files")
                        if not isinstance(unreadable, list) or any(not isinstance(path, str) for path in unreadable):
                            errors.append(
                                "metadata.source.import_summary.unreadable_files must be an array of strings"
                            )
                        elif any(_normalise_local_reference(path) is None for path in unreadable):
                            errors.append(
                                "metadata.source.import_summary.unreadable_files must contain safe relative paths"
                            )

    participants = archive.get("participants")
    if not isinstance(participants, list) or not participants:
        errors.append("participants must be a non-empty array")
        participant_ids: set[str] = set()
    else:
        participant_ids = set()
        for index, participant in enumerate(participants):
            if not isinstance(participant, dict):
                errors.append(f"participants[{index}] must be an object")
                continue
            participant_id = participant.get("id")
            if not isinstance(participant_id, str) or not participant_id.strip():
                errors.append(f"participants[{index}].id must be a non-empty string")
            elif participant_id in participant_ids:
                errors.append(f"participants[{index}].id duplicates {participant_id!r}")
            else:
                participant_ids.add(participant_id)
            if not isinstance(
                participant.get("display_name") or participant.get("username"), str
            ):
                errors.append(
                    f"participants[{index}] needs display_name or username as a string"
                )
            for field in ("display_name", "username", "avatar_alt"):
                if field in participant and participant[field] is not None and not isinstance(participant[field], str):
                    errors.append(f"participants[{index}].{field} must be a string or null")
            if "avatar_path" in participant:
                _validate_local_reference(participant["avatar_path"], f"participants[{index}].avatar_path", errors)
            if "avatar_ref" in participant and participant["avatar_ref"] is not None:
                if not isinstance(participant["avatar_ref"], str):
                    errors.append(f"participants[{index}].avatar_ref must be a string or null")
                elif not _is_http_url(participant["avatar_ref"]):
                    errors.append(f"participants[{index}].avatar_ref must be an HTTP(S) URL")

    messages = archive.get("messages")
    if not isinstance(messages, list):
        errors.append("messages must be an array")
        messages = []

    seen_message_ids: set[str] = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            errors.append(f"messages[{index}] must be an object")
            continue
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            errors.append(f"messages[{index}].id must be a non-empty string")
        elif message_id in seen_message_ids:
            errors.append(f"messages[{index}].id duplicates {message_id!r}")
        else:
            seen_message_ids.add(message_id)

        author_id = message.get("author_id")
        if not isinstance(author_id, str) or not author_id.strip():
            errors.append(f"messages[{index}].author_id must be a non-empty string")
        elif participant_ids and author_id not in participant_ids:
            errors.append(
                f"messages[{index}].author_id {author_id!r} is not in participants"
            )

        if parse_timestamp(message.get("timestamp")) is None:
            _validate_timestamp(message.get("timestamp"), f"messages[{index}].timestamp", errors)
        elif not _has_timestamp_timezone(message.get("timestamp")):
            _validate_timestamp(message.get("timestamp"), f"messages[{index}].timestamp", errors)
        if not isinstance(message.get("content", ""), str):
            errors.append(f"messages[{index}].content must be a string")
        if "source_display" in message and message["source_display"] is not None:
            _validate_source_display(message["source_display"], f"messages[{index}].source_display", errors)
        if "grouped" in message and not isinstance(message["grouped"], bool):
            errors.append(f"messages[{index}].grouped must be a boolean")
        if "attachments" in message and not isinstance(message["attachments"], list):
            errors.append(f"messages[{index}].attachments must be an array")
        elif isinstance(message.get("attachments"), list):
            for attachment_index, attachment in enumerate(message["attachments"]):
                _validate_attachment(attachment, f"messages[{index}].attachments[{attachment_index}]", errors)
        if "reactions" in message and not isinstance(message["reactions"], list):
            errors.append(f"messages[{index}].reactions must be an array")
        elif isinstance(message.get("reactions"), list):
            for reaction_index, reaction in enumerate(message["reactions"]):
                _validate_reaction(reaction, f"messages[{index}].reactions[{reaction_index}]", errors)
        if "embeds" in message and not isinstance(message["embeds"], list):
            errors.append(f"messages[{index}].embeds must be an array")
        elif isinstance(message.get("embeds"), list):
            for embed_index, embed in enumerate(message["embeds"]):
                _validate_embed(embed, f"messages[{index}].embeds[{embed_index}]", errors)
        for field in ("reply_to", "channel_id"):
            if field in message and message[field] is not None and not isinstance(message[field], str):
                errors.append(f"messages[{index}].{field} must be a string or null")
        if "message_link" in message and message["message_link"] is not None:
            if not isinstance(message["message_link"], str):
                errors.append(f"messages[{index}].message_link must be a string or null")
            elif not _is_http_url(message["message_link"]):
                errors.append(f"messages[{index}].message_link must be an HTTP(S) URL")
        if "edited_at" in message and message["edited_at"] is not None:
            _validate_timestamp(message["edited_at"], f"messages[{index}].edited_at", errors)
        if "provenance" in message:
            _validate_provenance(message["provenance"], f"messages[{index}].provenance", errors)

    return errors


def _first(record: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return default


def _normalise_attachment(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        local_path = None if _is_http_url(value) else (_normalise_local_reference(value) or value)
        return {
            "name": Path(value).name or "attachment",
            "url": value if _is_http_url(value) else None,
            "path": local_path,
            "mime": mimetypes.guess_type(value)[0] or "application/octet-stream",
        }
    if isinstance(value, dict):
        path = _first(value, "path", "local_path", "file", default=None)
        url = _first(value, "url", "proxy_url", default=None)
        name = _first(value, "name", "filename", default=None)
        local_path = None if _is_http_url(path) else (_normalise_local_reference(path) or path)
        if not _is_http_url(url) and _is_http_url(path):
            url = path
        return {
            "name": name or (Path(local_path).name if local_path else "attachment"),
            "path": local_path,
            "url": url,
            "mime": _first(value, "mime", "content_type", default=None)
            or (mimetypes.guess_type(str(local_path or url))[0] if local_path or url else None)
            or "application/octet-stream",
            "size_bytes": _first(value, "size_bytes", "size", default=None),
        }
    return {"name": "attachment", "path": None, "url": None, "mime": "application/octet-stream"}


def _normalise_reaction(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"emoji": value.strip() or "reaction", "count": 1}
    if not isinstance(value, dict):
        return {"emoji": str(value) if value is not None else "reaction", "count": 1}

    emoji = _first(value, "emoji", "Emoji", "name", "Name", "reaction", "Reaction", default=None)
    if isinstance(emoji, dict):
        emoji = _first(emoji, "name", "Name", "emoji", "Emoji", default=None)
    normalized: dict[str, Any] = {"emoji": str(emoji).strip() if emoji is not None else "reaction"}
    if not normalized["emoji"]:
        normalized["emoji"] = "reaction"
    count = _first(value, "count", "Count", "total", "Total", default=None)
    if isinstance(count, str) and count.strip().isdigit():
        count = int(count.strip())
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        normalized["count"] = count
    else:
        normalized["count"] = 1
    me = _first(value, "me", "Me", "is_me", "isMe", "mine", default=_MISSING)
    if isinstance(me, str) and me.strip().casefold() in {"true", "yes", "1"}:
        me = True
    elif isinstance(me, str) and me.strip().casefold() in {"false", "no", "0"}:
        me = False
    if isinstance(me, bool):
        normalized["me"] = me
    return normalized


def _normalise_embed(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"description": value}
    if not isinstance(value, dict):
        return {"description": str(value) if value is not None else "Embedded reference"}

    normalized: dict[str, Any] = {}
    for output_key, input_keys in (
        ("title", ("title", "Title")),
        ("description", ("description", "Description", "text", "Text")),
        ("footer", ("footer", "Footer")),
    ):
        field = _first(value, *input_keys, default=None)
        if isinstance(field, dict):
            field = _first(field, "text", "Text", "name", "Name", default=None)
        if field is not None and str(field).strip():
            normalized[output_key] = str(field)

    url = _first(value, "url", "URL", "link", "Link", default=None)
    if _is_http_url(url):
        normalized["url"] = url.strip()

    image = _first(
        value,
        "image_path",
        "local_path",
        "image",
        "Image",
        "image_url",
        "imageUrl",
        "Image URL",
        "thumbnail",
        "Thumbnail",
        default=None,
    )
    if isinstance(image, dict):
        image = _first(image, "path", "local_path", "url", "proxy_url", default=None)
    local_image = _normalise_local_reference(image)
    if local_image:
        normalized["image_path"] = local_image
    elif _is_http_url(image):
        normalized["image_url"] = image.strip()
    for key in ("type", "site_name", "provider"):
        field = _first(value, key, key.title(), default=None)
        if isinstance(field, str) and field.strip():
            normalized[key] = field.strip()
    return normalized


def _normalise_record(
    record: dict[str, Any],
    channel_id: str,
    index: int,
    source_file: str | None = None,
) -> dict[str, Any] | None:
    message_id = _first(record, "id", "ID", "message_id", "Message ID", default=None)
    raw_timestamp = _first(record, "timestamp", "Timestamp", "created_at", "Date", default=None)
    timestamp = normalise_timestamp(raw_timestamp)
    if message_id is None or not str(message_id).strip() or timestamp is None or not _has_timestamp_timezone(raw_timestamp):
        return None
    content = _first(record, "content", "Contents", "message", "Message", default="")
    if not isinstance(content, str):
        content = str(content)
    raw_attachments = _first(record, "attachments", "Attachments", default=[])
    if raw_attachments is None:
        raw_attachments = []
    elif not isinstance(raw_attachments, list):
        raw_attachments = [raw_attachments]
    raw_reactions = _first(record, "reactions", "Reactions", default=[])
    if raw_reactions is None:
        raw_reactions = []
    elif not isinstance(raw_reactions, list):
        raw_reactions = [raw_reactions]
    raw_embeds = _first(record, "embeds", "Embeds", default=[])
    if raw_embeds is None:
        raw_embeds = []
    elif not isinstance(raw_embeds, list):
        raw_embeds = [raw_embeds]
    message: dict[str, Any] = {
        "id": str(message_id),
        "author_id": "me",
        "timestamp": timestamp,
        "content": content,
        "channel_id": channel_id,
        "attachments": [_normalise_attachment(item) for item in raw_attachments],
        "reactions": [_normalise_reaction(item) for item in raw_reactions],
        "embeds": [_normalise_embed(item) for item in raw_embeds],
    }
    reply_to = _first(record, "reply_to", "Reply To", "replyTo", "reply_to_id", "reference", "Reference", default=None)
    if isinstance(reply_to, dict):
        reply_to = _first(reply_to, "id", "ID", "message_id", "messageId", default=None)
    if reply_to is not None and str(reply_to).strip():
        message["reply_to"] = str(reply_to).strip()
    message_link = _first(record, "message_link", "Message Link", "source_link", "sourceLink", default=None)
    if isinstance(message_link, str) and message_link.strip():
        message["message_link"] = message_link.strip()
    edited_at = _first(record, "edited_at", "Edited At", "editedAt", "edited", "Edited", default=None)
    if edited_at is not None:
        normalized_edited_at = normalise_timestamp(edited_at)
        if normalized_edited_at is not None and _has_timestamp_timezone(edited_at):
            message["edited_at"] = normalized_edited_at
    message["provenance"] = {
        "source_file": source_file,
        "record_index": index,
    }
    return message


def _data_package_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        nested = value.get("messages")
        if isinstance(nested, list):
            return nested
        if any(
            key in value
            for key in ("id", "ID", "message_id", "Message ID", "timestamp", "Timestamp", "created_at", "Date")
        ):
            return [value]
    return []


def _record_skip_reason(record: dict[str, Any]) -> str:
    message_id = _first(record, "id", "ID", "message_id", "Message ID", default=None)
    if message_id is None or not str(message_id).strip():
        return "missing_message_id"
    raw_timestamp = _first(record, "timestamp", "Timestamp", "created_at", "Date", default=None)
    if normalise_timestamp(raw_timestamp) is None:
        return "invalid_timestamp"
    if not _has_timestamp_timezone(raw_timestamp):
        return "timestamp_missing_timezone"
    return "unsupported_record"


def _transcript_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict):
        records_value = value.get("messages")
        if isinstance(records_value, list):
            records = records_value
        else:
            nested = value.get("transcript") or value.get("conversation") or value.get("data")
            if isinstance(nested, dict) and isinstance(nested.get("messages"), list):
                records = nested["messages"]
            elif any(key in value for key in ("id", "message_id", "timestamp", "created_at")):
                records = [value]
            else:
                raise ValueError("Transcript JSON must contain a messages array")
    else:
        raise ValueError("Transcript JSON must be an object or an array")
    invalid = [index for index, record in enumerate(records) if not isinstance(record, dict)]
    if invalid:
        raise ValueError(f"Transcript messages must be objects; invalid indexes: {invalid}")
    return records


def _transcript_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("metadata"), dict):
        return value["metadata"]
    return {}


def _raw_message_id(record: dict[str, Any], index: int) -> str | None:
    value = _first(record, "id", "ID", "message_id", "messageId", "Message ID", default=None)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _capture_range_summary(
    source_file: str,
    records: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    capture_range = metadata.get("capture_range")
    if not isinstance(capture_range, dict):
        capture_range = {}
    ordered = sorted(
        records,
        key=lambda record: (
            str(_first(record, "timestamp", "Timestamp", "created_at", "createdAt", "Date", default="")),
            str(_raw_message_id(record, 0) or ""),
        ),
    )
    first = ordered[0] if ordered else {}
    last = ordered[-1] if ordered else {}
    first_id = _raw_message_id(first, 0)
    last_id = _raw_message_id(last, 0)
    first_timestamp = _first(first, "timestamp", "Timestamp", "created_at", "createdAt", "Date", default=None)
    last_timestamp = _first(last, "timestamp", "Timestamp", "created_at", "createdAt", "Date", default=None)
    return {
        "source_file": source_file,
        "message_count": len(records),
        "message_ids": {message_id for message_id in (_raw_message_id(record, index) for index, record in enumerate(records)) if message_id},
        "oldest_message_id": capture_range.get("oldest_message_id") or first_id,
        "oldest_timestamp": capture_range.get("oldest_timestamp") or first_timestamp,
        "newest_message_id": capture_range.get("newest_message_id") or last_id,
        "newest_timestamp": capture_range.get("newest_timestamp") or last_timestamp,
        "at_start": bool(capture_range.get("at_start")),
        "at_end": bool(capture_range.get("at_end")),
        "has_capture_range": bool(capture_range),
    }


def _coverage_report(
    ranges: list[dict[str, Any]],
    duplicate_count: int = 0,
    conflict_count: int = 0,
    reached_start: bool = False,
    reached_end: bool = False,
) -> dict[str, Any]:
    ordered = sorted(
        ranges,
        key=lambda item: (
            str(item.get("oldest_timestamp") or ""),
            0 if item.get("at_start") else 1,
            str(item.get("source_file") or ""),
        ),
    )
    unlinked_ranges: list[str] = []
    public_ranges: list[dict[str, Any]] = []
    previous_ids: set[str] | None = None
    for item in ordered:
        ids = item.get("message_ids") if isinstance(item.get("message_ids"), set) else set()
        overlap = len(previous_ids & ids) if previous_ids is not None else 0
        if previous_ids is not None and overlap == 0:
            unlinked_ranges.append(f"{public_ranges[-1]['source_file']} -> {item['source_file']}")
        public_ranges.append({
            key: item.get(key)
            for key in (
                "source_file",
                "message_count",
                "oldest_message_id",
                "oldest_timestamp",
                "newest_message_id",
                "newest_timestamp",
                "at_start",
                "at_end",
                "has_capture_range",
            )
        } | {"overlap_with_previous": overlap})
        previous_ids = ids

    first = ordered[0] if ordered else {}
    last = ordered[-1] if ordered else {}
    earliest_timestamp = str(first.get("oldest_timestamp") or "")
    latest_timestamp = max((str(item.get("newest_timestamp") or "") for item in ordered), default="")
    start_confirmed = bool(
        reached_start
        or any(
            item.get("at_start") and str(item.get("oldest_timestamp") or "") == earliest_timestamp
            for item in ordered
        )
    )
    end_confirmed = bool(
        reached_end
        or any(
            item.get("at_end") and str(item.get("newest_timestamp") or "") == latest_timestamp
            for item in ordered
        )
    )
    all_ranges_tagged = bool(ordered) and all(item.get("has_capture_range") for item in ordered)
    linked = all_ranges_tagged and not unlinked_ranges
    if start_confirmed and end_confirmed and linked and conflict_count == 0:
        status = "verified"
    elif start_confirmed or end_confirmed:
        status = "partial"
    else:
        status = "unverified"
    notes: list[str] = []
    if not start_confirmed:
        notes.append("The oldest boundary has not been confirmed; capture a range at the top of the DM.")
    if not end_confirmed:
        notes.append("The newest boundary has not been confirmed; capture a range at the bottom of the DM.")
    if unlinked_ranges:
        notes.append("Adjacent capture ranges do not overlap; recapture those transitions with at least one shared message.")
    if conflict_count:
        notes.append("Overlapping ranges contain conflicting records; review the source captures before treating coverage as complete.")
    notes.append("Range verification cannot prove messages that Discord failed to render or messages deleted before capture.")
    if conflict_count:
        next_action = "Review the conflicting overlap records before rebuilding the archive."
    elif unlinked_ranges:
        next_action = "Recapture each gap with at least one message shared by the adjacent ranges, then merge again."
    elif not start_confirmed and not end_confirmed:
        next_action = "Return to the open DM, reach both the oldest and newest boundaries, and capture overlapping ranges at each end."
    elif not start_confirmed:
        next_action = "Return to the open DM, scroll to the oldest message until Discord stops loading older history, then capture an overlapping range."
    elif not end_confirmed:
        next_action = "Return to the open DM, scroll to the newest message, then capture an overlapping range at the end."
    elif not linked:
        next_action = "Merge overlapping ranges so every transition shares at least one message."
    else:
        next_action = "No further capture step is required for the observed rendered range."
    return {
        "version": 1,
        "status": status,
        "complete": status == "verified",
        "range_count": len(ordered),
        "unique_message_count": len(set().union(*(item.get("message_ids", set()) for item in ordered))) if ordered else 0,
        "duplicate_message_count": duplicate_count,
        "conflict_count": conflict_count,
        "start_confirmed": start_confirmed,
        "end_confirmed": end_confirmed,
        "ranges_linked": linked,
        "unlinked_ranges": unlinked_ranges,
        "ranges": public_ranges,
        "notes": notes,
        "next_action": next_action,
    }


def merge_transcripts(
    input_paths: list[Path],
    output_path: Path,
    reached_start: bool = False,
    reached_end: bool = False,
) -> dict[str, Any]:
    """Merge overlapping, user-captured transcript ranges into one transcript.

    Inputs are expected to be captures from the same open DM and stored beside
    the output so relative local asset references remain portable. Message IDs
    deduplicate overlapping ranges; conflicting duplicates are retained once and
    reported in coverage metadata rather than silently overwritten.
    """

    if not input_paths:
        raise ValueError("At least one transcript capture is required")
    resolved_inputs = [path.resolve() for path in input_paths]
    output_path = output_path.resolve()
    if any(path.parent != output_path.parent for path in resolved_inputs):
        raise ValueError("merge-transcripts requires captures and output in the same directory")
    if len({str(path) for path in resolved_inputs}) != len(resolved_inputs):
        raise ValueError("merge-transcripts received the same capture more than once")

    first_value = load_json(resolved_inputs[0])
    first_metadata = _transcript_metadata(first_value)
    merged_metadata = dict(first_metadata)
    merged_source = dict(first_metadata.get("source") or {})
    merged_source["label"] = "Merged Discord visible conversation captures"
    merged_source["type"] = "user_supplied_transcript"
    merged_source["capture_files"] = [path.name for path in resolved_inputs]
    merged_notes = list(merged_source.get("notes") or [])
    merged_notes.append("Merged from overlapping, user-controlled visible Discord ranges.")
    merged_source["notes"] = merged_notes
    merged_metadata["source"] = merged_source
    merged_metadata.pop("capture_range", None)

    participants_by_id: dict[str, dict[str, Any]] = {}
    messages_by_id: dict[str, dict[str, Any]] = {}
    message_order: list[str] = []
    ranges: list[dict[str, Any]] = []
    duplicate_count = 0
    conflict_count = 0
    channel_ids: set[str] = set()

    def message_fingerprint(record: dict[str, Any]) -> str:
        comparable = {
            key: value
            for key, value in record.items()
            if key not in {"grouped", "source_display"}
        }
        for collection, url_key in (("attachments", "url"), ("embeds", "image_url")):
            values = comparable.get(collection)
            if not isinstance(values, list):
                continue
            normalized_values = []
            for value in values:
                if isinstance(value, dict):
                    normalized_value = dict(value)
                    if url_key in normalized_value:
                        normalized_value[url_key] = _canonical_remote_reference(normalized_value[url_key])
                    normalized_values.append(normalized_value)
                else:
                    normalized_values.append(value)
            comparable[collection] = normalized_values
        return json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def refresh_signed_references(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
        for collection, url_key in (("attachments", "url"), ("embeds", "image_url")):
            existing_values = existing.get(collection)
            incoming_values = incoming.get(collection)
            if not isinstance(existing_values, list) or not isinstance(incoming_values, list):
                continue
            for incoming_value in incoming_values:
                if not isinstance(incoming_value, dict) or not _remote_media_allowed(incoming_value.get(url_key)):
                    continue
                incoming_identity = _canonical_remote_reference(incoming_value[url_key])
                for existing_value in existing_values:
                    if not isinstance(existing_value, dict):
                        continue
                    if _canonical_remote_reference(existing_value.get(url_key)) == incoming_identity:
                        existing_value[url_key] = incoming_value[url_key]

    for capture_path in resolved_inputs:
        value = load_json(capture_path)
        records = _transcript_records(value)
        metadata = _transcript_metadata(value)
        ranges.append(_capture_range_summary(capture_path.name, records, metadata))
        channel_id = metadata.get("channel_id")
        if channel_id is not None:
            channel_ids.add(str(channel_id))
        for participant in value.get("participants", []) if isinstance(value, dict) and isinstance(value.get("participants"), list) else []:
            if not isinstance(participant, dict):
                continue
            participant_id = str(participant.get("id") or participant.get("username") or participant.get("display_name") or "").strip()
            if not participant_id:
                continue
            existing = participants_by_id.setdefault(participant_id, {})
            for key, item in participant.items():
                if item not in (None, "") and existing.get(key) in (None, ""):
                    existing[key] = item
        for index, record in enumerate(records):
            message_id = _raw_message_id(record, index)
            key = message_id or f"{capture_path.name}:record-{index + 1:06d}"
            existing = messages_by_id.get(key)
            if existing is None:
                messages_by_id[key] = dict(record)
                message_order.append(key)
                continue
            duplicate_count += 1
            if message_fingerprint(existing) != message_fingerprint(record):
                conflict_count += 1
            refresh_signed_references(existing, record)
            existing_display = existing.get("source_display")
            incoming_display = record.get("source_display")
            if isinstance(incoming_display, dict):
                if not isinstance(existing_display, dict):
                    existing["source_display"] = dict(incoming_display)
                else:
                    for key, value in incoming_display.items():
                        if existing_display.get(key) in (None, "") and value not in (None, ""):
                            existing_display[key] = value
            if existing.get("grouped") is True and record.get("grouped") is False:
                existing["grouped"] = False

    if len(channel_ids) > 1:
        raise ValueError(f"Capture files contain multiple channel IDs: {sorted(channel_ids)}")
    if channel_ids:
        merged_metadata["channel_id"] = next(iter(channel_ids))
    coverage = _coverage_report(
        ranges,
        duplicate_count=duplicate_count,
        conflict_count=conflict_count,
        reached_start=reached_start,
        reached_end=reached_end,
    )
    merged_metadata["coverage"] = coverage
    merged = {
        "metadata": merged_metadata,
        "participants": list(participants_by_id.values()),
        "messages": [messages_by_id[key] for key in message_order],
    }
    write_json(output_path, merged)
    return {
        "messages": len(merged["messages"]),
        "participants": len(merged["participants"]),
        "duplicates": duplicate_count,
        "conflicts": conflict_count,
        "coverage": coverage,
    }


def verify_transcript_coverage(input_path: Path) -> dict[str, Any]:
    """Read coverage metadata and return a stable verification report."""

    archive = load_json(input_path.resolve())
    metadata = _transcript_metadata(archive)
    coverage = metadata.get("coverage")
    if isinstance(coverage, dict):
        return coverage
    capture_range = metadata.get("capture_range")
    return {
        "version": 1,
        "status": "partial" if isinstance(capture_range, dict) else "unverified",
        "complete": False,
        "range_count": 1 if isinstance(capture_range, dict) else 0,
        "unique_message_count": len(_transcript_records(archive)),
        "duplicate_message_count": 0,
        "conflict_count": 0,
        "start_confirmed": bool(isinstance(capture_range, dict) and capture_range.get("at_start")),
        "end_confirmed": bool(isinstance(capture_range, dict) and capture_range.get("at_end")),
        "ranges_linked": False,
        "unlinked_ranges": [],
        "ranges": [],
        "notes": ["This archive has not been produced by the overlap-aware range merge workflow."],
        "next_action": "Return to the open DM, capture overlapping ranges at the oldest and newest boundaries, then merge them before treating the archive as complete.",
    }


def _stable_identifier(value: str, fallback_index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return f"author-{slug or fallback_index + 1}"


def _normalise_transcript_participant(record: dict[str, Any], index: int) -> dict[str, Any]:
    participant_id = _first(record, "id", "user_id", "author_id", "userId", default=None)
    display_name = _first(record, "display_name", "displayName", "name", "label", default=None)
    username = _first(record, "username", "user_name", "handle", default=None)
    if participant_id is None:
        participant_id = _stable_identifier(str(display_name or username or "participant"), index)
    if display_name is None:
        display_name = username or str(participant_id)
    if not isinstance(display_name, str):
        display_name = str(display_name)
    participant: dict[str, Any] = {
        "id": str(participant_id),
        "display_name": display_name,
    }
    if username is not None:
        participant["username"] = str(username)
    avatar = _first(record, "avatar_path", "avatar_file", "avatar", default=None)
    avatar_ref = _first(record, "avatar_ref", "avatar_url", default=None)
    if isinstance(avatar, dict):
        avatar = _first(avatar, "path", "local_path", "url", default=None)
    if _is_http_url(avatar):
        avatar_ref = avatar
    elif avatar is not None:
        participant["avatar_path"] = _normalise_local_reference(avatar) or avatar
    if _is_http_url(avatar_ref):
        participant["avatar_ref"] = avatar_ref
    avatar_alt = _first(record, "avatar_alt", "avatarAlt", default=None)
    if avatar_alt is not None:
        participant["avatar_alt"] = str(avatar_alt)
    return participant


def _author_details(record: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    author_value = _first(record, "author", "Author", "user", default=_MISSING)
    author_object = author_value if isinstance(author_value, dict) else {}
    explicit_id = _first(record, "author_id", "authorId", "user_id", "userId", default=_MISSING)
    if explicit_id is _MISSING:
        explicit_id = _first(author_object, "id", "user_id", "author_id", "userId", default=_MISSING)
    if explicit_id is not _MISSING and explicit_id is not None:
        raw_id = str(explicit_id)
        generated = False
    elif isinstance(author_value, str) and author_value.strip():
        raw_id = author_value.strip()
        generated = True
    elif author_object:
        author_label = _first(author_object, "display_name", "displayName", "name", "username", default=None)
        if author_label is None or not str(author_label).strip():
            raise ValueError("message is missing an author_id or author object with an id")
        raw_id = str(author_label).strip()
        generated = True
    else:
        raise ValueError("message is missing an author_id or author object with an id")
    details = {
        "id": raw_id,
        "display_name": _first(author_object, "display_name", "displayName", "name", default=None),
        "username": _first(author_object, "username", "user_name", "handle", default=None),
        "avatar_path": _first(author_object, "avatar_path", "avatar_file", "avatar_ref", "avatar_url", default=None),
        "avatar_alt": _first(author_object, "avatar_alt", "avatarAlt", default=None),
    }
    if details["display_name"] is None and details["username"] is None and isinstance(author_value, str):
        details["display_name"] = author_value.strip()
    return raw_id, generated, details


def _normalise_required_transcript_timestamp(value: Any, index: int) -> str:
    timestamp = normalise_timestamp(value)
    if timestamp is None or not _has_timestamp_timezone(value):
        raise ValueError(f"message {index} timestamp must be an ISO-8601 timestamp with a timezone")
    return timestamp


def _normalise_transcript_message(
    record: dict[str, Any],
    index: int,
    source_file: str,
    participant_aliases: dict[str, str],
    participants: list[dict[str, Any]],
    participant_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw_id = _first(record, "id", "ID", "message_id", "messageId", "Message ID", default=None)
    id_generated = raw_id is None or not str(raw_id).strip()
    message_id = str(raw_id).strip() if not id_generated else f"transcript-{index + 1:06d}"
    timestamp = _normalise_required_transcript_timestamp(
        _first(record, "timestamp", "Timestamp", "created_at", "createdAt", "Date", default=None),
        index,
    )
    author_key, generated_author, author_details = _author_details(record)
    resolved_author_id = participant_aliases.get(author_key)
    if resolved_author_id is None:
        resolved_author_id = participant_aliases.get(author_key.casefold())
    if resolved_author_id is None:
        resolved_author_id = _stable_identifier(author_key, index) if generated_author else author_key
        if resolved_author_id not in participant_by_id:
            details = {key: value for key, value in author_details.items() if value is not None}
            details["id"] = resolved_author_id
            participant = _normalise_transcript_participant(details, len(participants))
            participant_by_id[resolved_author_id] = participant
            participants.append(participant)
            for alias in (author_key, participant.get("id"), participant.get("username"), participant.get("display_name")):
                if isinstance(alias, str) and alias:
                    participant_aliases[alias] = resolved_author_id
                    participant_aliases[alias.casefold()] = resolved_author_id

    content = _first(record, "content", "Contents", "message", "Message", default="")
    if not isinstance(content, str):
        content = str(content)
    raw_attachments = _first(record, "attachments", "Attachments", default=[])
    if raw_attachments is None:
        raw_attachments = []
    elif not isinstance(raw_attachments, list):
        raw_attachments = [raw_attachments]
    raw_reactions = _first(record, "reactions", "Reactions", default=[])
    raw_embeds = _first(record, "embeds", "Embeds", default=[])
    if raw_reactions is None:
        raw_reactions = []
    if raw_embeds is None:
        raw_embeds = []
    message: dict[str, Any] = {
        "id": message_id,
        "author_id": resolved_author_id,
        "timestamp": timestamp,
        "content": content,
        "attachments": [_normalise_attachment(item) for item in raw_attachments],
        "reactions": [_normalise_reaction(item) for item in (raw_reactions if isinstance(raw_reactions, list) else [raw_reactions])],
        "embeds": [_normalise_embed(item) for item in (raw_embeds if isinstance(raw_embeds, list) else [raw_embeds])],
        "provenance": {
            "source_file": source_file,
            "record_index": index,
        },
    }
    raw_source_display = _first(record, "source_display", "sourceDisplay", default=None)
    if isinstance(raw_source_display, dict):
        source_display = {
            key: value.strip()
            for key, value in raw_source_display.items()
            if key in {"label", "date", "time"} and isinstance(value, str) and value.strip()
        }
        if source_display:
            message["source_display"] = source_display
    elif isinstance(raw_source_display, str) and raw_source_display.strip():
        message["source_display"] = {"label": raw_source_display.strip()}
    if id_generated:
        message["provenance"]["id_generated"] = True
    reply_to = _first(record, "reply_to", "Reply To", "replyTo", "reply_to_id", "reference", "Reference", default=None)
    if isinstance(reply_to, dict):
        reply_to = _first(reply_to, "id", "ID", "message_id", "messageId", default=None)
    if reply_to is not None and str(reply_to).strip():
        message["reply_to"] = str(reply_to).strip()
    message_link = _first(record, "message_link", "Message Link", "source_link", "sourceLink", default=None)
    if isinstance(message_link, str) and message_link.strip():
        message["message_link"] = message_link.strip()
    edited_at = _first(record, "edited_at", "Edited At", "editedAt", "edited", "Edited", default=None)
    if edited_at is not None:
        message["edited_at"] = _normalise_required_transcript_timestamp(edited_at, index)
    channel_id = _first(record, "channel_id", "channelId", default=None)
    if channel_id is not None:
        message["channel_id"] = str(channel_id)
    grouped = _first(record, "grouped", "is_grouped", default=None)
    if isinstance(grouped, bool):
        message["grouped"] = grouped
    return message


def import_transcript(input_path: Path, output_path: Path) -> dict[str, Any]:
    """Create an archive from a user-supplied transcript JSON file.

    This adapter accepts a JSON object with a ``messages`` array (or a bare
    message array), plus optional participants and metadata. It never connects
    to Discord, downloads remote assets, or infers account credentials.
    """

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise ValueError(f"Transcript file does not exist: {input_path}")
    with input_path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    records = _transcript_records(value)
    input_metadata = value.get("metadata", {}) if isinstance(value, dict) and isinstance(value.get("metadata"), dict) else {}
    raw_participants = value.get("participants", []) if isinstance(value, dict) else []
    if not isinstance(raw_participants, list):
        raise ValueError("Transcript participants must be an array when provided")

    participants: list[dict[str, Any]] = []
    participant_by_id: dict[str, dict[str, Any]] = {}
    participant_aliases: dict[str, str] = {}
    for index, participant_record in enumerate(raw_participants):
        if not isinstance(participant_record, dict):
            raise ValueError(f"Transcript participants[{index}] must be an object")
        participant = _normalise_transcript_participant(participant_record, index)
        if participant["id"] in participant_by_id:
            raise ValueError(f"Transcript participants[{index}] duplicates {participant['id']!r}")
        participant_by_id[participant["id"]] = participant
        participants.append(participant)
        for alias in (participant.get("id"), participant.get("username"), participant.get("display_name")):
            if isinstance(alias, str) and alias:
                participant_aliases[alias] = participant["id"]
                participant_aliases[alias.casefold()] = participant["id"]

    source_file = input_path.name
    messages = [
        _normalise_transcript_message(
            record,
            index,
            source_file,
            participant_aliases,
            participants,
            participant_by_id,
        )
        for index, record in enumerate(records)
    ]
    messages.sort(key=lambda item: (item["timestamp"], item["id"]))

    source_input = input_metadata.get("source") if isinstance(input_metadata.get("source"), dict) else {}
    notes = [
        "Imported from a user-supplied transcript JSON file.",
        "No Discord login, user token, crawler, or remote asset download was used.",
        "Per-message source_file and record_index preserve the input record location.",
    ]
    provided_notes = source_input.get("notes") if isinstance(source_input, dict) else None
    if isinstance(provided_notes, list):
        notes.extend(str(note) for note in provided_notes if isinstance(note, str) and note.strip())
    metadata: dict[str, Any] = {
        "kind": input_metadata.get("kind") or (value.get("kind") if isinstance(value, dict) else None) or "conversation",
        "title": input_metadata.get("title") or (value.get("title") if isinstance(value, dict) else None) or "Imported conversation",
        "channel_id": input_metadata.get("channel_id") if "channel_id" in input_metadata else (value.get("channel_id") if isinstance(value, dict) else None),
        "captured_at": input_metadata.get("captured_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "display_timezone": input_metadata.get("display_timezone") or "UTC",
        "source": {
            "type": "user_supplied_transcript",
            "label": source_input.get("label") or "User-supplied transcript",
            "source_name": source_file,
            "notes": notes,
        },
    }
    channel_handle = input_metadata.get("channel_handle")
    if isinstance(channel_handle, str) and channel_handle.strip():
        metadata["channel_handle"] = channel_handle.strip().lstrip("@")
    for field in ("capture_range", "coverage"):
        if isinstance(input_metadata.get(field), dict):
            metadata[field] = input_metadata[field]
    if input_metadata.get("synthetic") is True or (isinstance(value, dict) and value.get("synthetic") is True):
        metadata["synthetic"] = True
    archive = {
        "schema_version": SCHEMA_VERSION,
        "metadata": metadata,
        "participants": participants,
        "messages": messages,
    }
    errors = validate_archive(archive)
    if errors:
        raise ValueError("Imported transcript did not produce a valid archive:\n- " + "\n- ".join(errors))
    write_json(output_path, archive)
    return archive


def materialize_remote_media(input_path: Path, output_path: Path) -> dict[str, int]:
    """Copy allowed remote image references into a private archive directory.

    This is an explicit, attended step. It only follows URLs already present in
    the archive and only permits Discord's CDN hosts; it never logs in, crawls,
    searches, or discovers additional media.
    """

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path.parent != output_path.parent:
        raise ValueError("materialize-media requires input and output archives in the same directory")
    archive = load_json(input_path)
    errors = validate_archive(archive)
    if errors:
        raise ValueError("Cannot materialize media from an invalid archive:\n- " + "\n- ".join(errors))

    source_root = input_path.parent
    assets_root = source_root / "assets"
    downloaded = 0
    reused = 0
    skipped = 0
    bytes_written = 0
    new_paths: list[Path] = []

    def materialize(url: Any, category: str, stem: str, name: Any, mime: Any) -> str | None:
        nonlocal downloaded, reused, skipped, bytes_written
        if not _is_http_url(url):
            return None
        if not _remote_media_allowed(url):
            skipped += 1
            return None
        extension = Path(urlparse(str(url)).path).suffix
        if not extension:
            extension = mimetypes.guess_extension(str(mime or "")) or ".bin"
        filename = _safe_media_filename(name or url, f"{stem}{extension}")
        if not Path(filename).suffix:
            filename = f"{filename}{extension}"
        digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:12]
        destination = assets_root / category / f"{digest}-{filename}"
        if destination.is_file():
            reused += 1
        else:
            bytes_written += _download_remote_media(str(url), destination)
            downloaded += 1
            new_paths.append(destination)
        return destination.relative_to(source_root).as_posix()

    for participant in archive.get("participants", []):
        if not isinstance(participant, dict) or participant.get("avatar_path") or not participant.get("avatar_ref"):
            continue
        path = materialize(
            participant.get("avatar_ref"),
            "avatars",
            str(participant.get("id") or "avatar"),
            f"{participant.get('id') or 'avatar'}.webp",
            "image/webp",
        )
        if path:
            participant["avatar_path"] = path

    for message in archive.get("messages", []):
        if not isinstance(message, dict):
            continue
        for attachment in message.get("attachments", []):
            if not isinstance(attachment, dict) or attachment.get("path") or not attachment.get("url"):
                continue
            mime = str(attachment.get("mime") or mimetypes.guess_type(str(attachment.get("url")))[0] or "")
            if not mime.startswith("image/"):
                continue
            path = materialize(
                attachment.get("url"),
                "attachments",
                str(message.get("id") or "attachment"),
                attachment.get("name"),
                mime,
            )
            if path:
                attachment["path"] = path
                if not attachment.get("size_bytes"):
                    attachment["size_bytes"] = (source_root / path).stat().st_size
        for embed in message.get("embeds", []):
            if not isinstance(embed, dict) or embed.get("image_path") or not embed.get("image_url"):
                continue
            path = materialize(
                embed.get("image_url"),
                "embeds",
                str(message.get("id") or "embed"),
                f"{message.get('id') or 'embed'}.png",
                "image/png",
            )
            if path:
                embed["image_path"] = path

    metadata = archive.setdefault("metadata", {})
    source = metadata.get("source")
    if not isinstance(source, dict):
        source = {}
        metadata["source"] = source
    notes = source.setdefault("notes", [])
    note = "Remote Discord CDN image references were explicitly copied into local archive assets; original URLs were retained."
    if note not in notes:
        notes.append(note)
    errors = validate_archive(archive)
    if errors:
        for path in new_paths:
            path.unlink(missing_ok=True)
        raise ValueError("Materialized archive is invalid:\n- " + "\n- ".join(errors))
    write_json(output_path, archive)
    return {
        "downloaded": downloaded,
        "reused": reused,
        "skipped": skipped,
        "bytes_written": bytes_written,
    }


def import_data_package(source_dir: Path, output_path: Path) -> dict[str, Any]:
    """Create a normalized archive from message JSON files in a data package.

    Discord's package formats have changed over time, so this deliberately
    accepts both the current JSON shape and older files with capitalized fields.
    It never logs in to Discord or fetches remote attachments.
    """

    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"Data package directory does not exist: {source_dir}")

    records: list[dict[str, Any]] = []
    files_scanned = 0
    files_with_records = 0
    records_seen = 0
    records_skipped = 0
    skipped_record_reasons: dict[str, int] = {}
    unreadable_files: list[str] = []
    for json_path in sorted((source_dir / "messages").rglob("*.json")) if (source_dir / "messages").exists() else []:
        files_scanned += 1
        source_file = json_path.relative_to(source_dir).as_posix()
        try:
            with json_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            unreadable_files.append(source_file)
            continue
        channel_id = json_path.parent.name
        items = _data_package_items(value)
        if items:
            files_with_records += 1
        for index, item in enumerate(items):
            records_seen += 1
            if not isinstance(item, dict):
                reason = "non_object_record"
                records_skipped += 1
                skipped_record_reasons[reason] = skipped_record_reasons.get(reason, 0) + 1
                continue
            record = item
            normalised = _normalise_record(record, channel_id, index, source_file)
            if normalised:
                records.append(normalised)
            else:
                reason = _record_skip_reason(record)
                records_skipped += 1
                skipped_record_reasons[reason] = skipped_record_reasons.get(reason, 0) + 1

    records.sort(key=lambda item: (item["timestamp"], item["id"]))
    channel_ids = sorted({record["channel_id"] for record in records})
    title = "Discord Data Package"
    if len(channel_ids) == 1:
        title = f"Discord DM {channel_ids[0]}"
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    import_summary = {
        "files_scanned": files_scanned,
        "files_with_records": files_with_records,
        "records_seen": records_seen,
        "records_imported": len(records),
        "records_skipped": records_skipped,
        "skipped_record_reasons": dict(sorted(skipped_record_reasons.items())),
        "unreadable_files": sorted(unreadable_files),
    }
    diagnostics_note = []
    if records_skipped or unreadable_files:
        diagnostics_note.append(
            "Import diagnostics are available in metadata.source.import_summary."
        )
    archive = {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "kind": "direct_message",
            "title": title,
            "channel_id": channel_ids[0] if len(channel_ids) == 1 else None,
            "captured_at": captured_at,
            "display_timezone": "UTC",
            "source": {
                "type": "discord_data_package",
                "label": "Discord Data Package",
                "source_name": source_dir.name or "DiscordDataPackage",
                "notes": [
                    "This import contains messages available in the supplied package.",
                    "Discord Data Packages contain messages sent by the requesting account.",
                    "Remote attachment URLs are preserved as provenance and are not downloaded.",
                    "Absolute source paths are omitted from the archive for portability and privacy.",
                    *diagnostics_note,
                ],
                "import_summary": import_summary,
            },
        },
        "participants": [
            {
                "id": "me",
                "username": "You",
                "display_name": "You",
                "avatar_path": None,
            }
        ],
        "messages": [
            {
                key: value
                for key, value in record.items()
                if key != "channel_id"
            }
            for record in records
        ],
    }
    errors = validate_archive(archive)
    if errors:
        raise ValueError("Imported package did not produce a valid archive:\n- " + "\n- ".join(errors))
    write_json(output_path, archive)
    return archive


def _asset_references(archive: dict[str, Any]) -> set[str]:
    references: set[str] = set()

    def collect(value: Any) -> None:
        if not isinstance(value, str) or value.startswith(("http://", "https://", "data:")):
            return
        reference = _normalise_local_reference(value)
        if reference:
            references.add(reference)

    participants = archive.get("participants", [])
    if not isinstance(participants, list):
        participants = []
    for participant in participants:
        if isinstance(participant, dict):
            collect(participant.get("avatar_path"))
            collect(participant.get("avatar_ref"))

    messages = archive.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        collect(message.get("avatar_ref"))
        attachments = message.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []
        for attachment in attachments:
            if isinstance(attachment, dict):
                collect(attachment.get("path"))
                collect(attachment.get("local_path"))
        embeds = message.get("embeds", [])
        if not isinstance(embeds, list):
            embeds = []
        for embed in embeds:
            if isinstance(embed, dict):
                collect(embed.get("image_path"))
                collect(embed.get("local_path"))
    return references


def _copy_referenced_assets(archive: dict[str, Any], source_root: Path, output_root: Path) -> list[str]:
    missing: list[str] = []
    source_root = source_root.resolve()
    for reference in sorted(_asset_references(archive)):
        source = (source_root / reference).resolve()
        try:
            source.relative_to(source_root)
        except ValueError:
            missing.append(reference)
            continue
        destination = output_root / reference
        if not source.is_file():
            missing.append(reference)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return missing


def _copy_viewer_assets(template: Path, output_root: Path) -> list[str]:
    assets_root = template.parent / "assets"
    if not assets_root.is_dir():
        return []
    copied: list[str] = []
    for source in sorted(path for path in assets_root.rglob("*") if path.is_file()):
        relative = source.relative_to(assets_root)
        destination = output_root / "assets" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append((Path("assets") / relative).as_posix())
    return copied


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_manifest(archive: dict[str, Any], output_dir: Path, viewer_assets: list[str] | None = None) -> dict[str, Any]:
    references = {"archive.json", "app.js", "index.html"}
    references.update(_asset_references(archive))
    references.update(viewer_assets or [])
    files: list[dict[str, Any]] = []
    for reference in sorted(references):
        path = output_dir / reference
        if not path.is_file():
            continue
        files.append(
            {
                "path": reference,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "manifest_version": 1,
        "archive_schema_version": archive["schema_version"],
        "files": files,
    }


def verify_build(output_dir: Path) -> list[str]:
    """Verify a generated offline viewer and its normalized archive contents."""

    output_dir = output_dir.resolve()
    errors: list[str] = []
    if not output_dir.is_dir():
        return [f"viewer directory does not exist: {output_dir}"]

    manifest_path = output_dir / "manifest.json"
    manifest_is_object = False
    manifest_paths: set[str] = set()
    if not manifest_path.is_file():
        errors.append("manifest.json is missing")
        manifest: dict[str, Any] = {}
    else:
        try:
            manifest_value = load_json(manifest_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            manifest_value = {}
            errors.append(f"manifest.json could not be read: {error}")
        if not isinstance(manifest_value, dict):
            errors.append("manifest.json must be a JSON object")
            manifest = {}
        else:
            manifest = manifest_value
            manifest_is_object = True

    if manifest_is_object:
        if manifest.get("manifest_version") != 1:
            errors.append("manifest_version must be 1")
        if manifest.get("archive_schema_version") != SCHEMA_VERSION:
            errors.append(f"manifest archive_schema_version must be {SCHEMA_VERSION}")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            errors.append("manifest.files must be an array")
            entries = []
        seen_paths: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(f"manifest.files[{index}] must be an object")
                continue
            reference = entry.get("path")
            if not isinstance(reference, str) or _normalise_local_reference(reference) != reference:
                errors.append(f"manifest.files[{index}].path must be a safe relative path")
                continue
            if reference in seen_paths:
                errors.append(f"manifest.files[{index}].path duplicates {reference!r}")
                continue
            seen_paths.add(reference)
            manifest_paths.add(reference)
            path = (output_dir / reference).resolve()
            try:
                path.relative_to(output_dir)
            except ValueError:
                errors.append(f"manifest.files[{index}].path escapes the viewer directory")
                continue
            if not path.is_file():
                errors.append(f"missing generated file: {reference}")
                continue
            size_bytes = entry.get("size_bytes")
            if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
                errors.append(f"manifest.files[{index}].size_bytes must be a non-negative integer")
            elif path.stat().st_size != size_bytes:
                errors.append(f"size mismatch: {reference}")
            expected_hash = entry.get("sha256")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64:
                errors.append(f"manifest.files[{index}].sha256 must be a SHA-256 hex digest")
            elif _sha256(path) != expected_hash:
                errors.append(f"hash mismatch: {reference}")
        required = {"archive.json", "app.js", "index.html"}
        missing_required = sorted(required - seen_paths)
        errors.extend(f"manifest is missing required file: {reference}" for reference in missing_required)

    archive_path = output_dir / "archive.json"
    if not archive_path.is_file():
        errors.append("archive.json is missing")
    else:
        try:
            archive = load_json(archive_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            archive = None
            errors.append(f"archive.json could not be read: {error}")
        if isinstance(archive, dict):
            errors.extend(f"archive: {error}" for error in validate_archive(archive))
            expected_files = {"archive.json", "app.js", "index.html"} | _asset_references(archive)
            errors.extend(
                f"manifest is missing expected file: {reference}"
                for reference in sorted(expected_files - manifest_paths)
            )
            for reference in sorted(_asset_references(archive)):
                if not (output_dir / reference).is_file():
                    errors.append(f"missing referenced local asset: {reference}")
        elif archive is not None:
            errors.append("archive: archive must be a JSON object")

    for required in ("index.html", "app.js"):
        if not (output_dir / required).is_file():
            errors.append(f"{required} is missing")
    return errors


def _template_path() -> Path:
    package_template = Path(__file__).resolve().parent / "viewer" / "template.html"
    source_template = Path(__file__).resolve().parents[2] / "viewer" / "template.html"
    installed_template = Path(sysconfig.get_path("data")) / "viewer" / "template.html"
    for candidate in (package_template, source_template, installed_template):
        if candidate.is_file():
            return candidate
    return source_template


def build_archive(input_path: Path, output_dir: Path, template_path: Path | None = None) -> list[str]:
    archive = load_json(input_path)
    errors = validate_archive(archive)
    if errors:
        raise ValueError("Archive validation failed:\n- " + "\n- ".join(errors))

    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "archive.json", archive)
    missing = _copy_referenced_assets(archive, input_path.parent, output_dir)

    template = template_path or _template_path()
    if not template.is_file():
        raise FileNotFoundError(f"Viewer template not found: {template}")
    viewer_assets = _copy_viewer_assets(template, output_dir)
    template_text = template.read_text(encoding="utf-8")
    # Keep the viewer source in one template, but emit its executable code as
    # an external local file so strict offline/browser CSPs do not block it.
    script_open = "  <script>\n"
    script_start = template_text.rfind(script_open)
    script_end = template_text.rfind("\n  </script>", script_start)
    if script_start == -1 or script_end == -1:
        raise ValueError("Viewer template is missing its executable script block")
    app_script = template_text[script_start + len(script_open) : script_end]
    template_text = (
        template_text[:script_start]
        + '  <script src="app.js" defer></script>'
        + template_text[script_end + len("\n  </script>") :]
    )
    payload = json.dumps(archive, ensure_ascii=False, separators=(",", ":"))
    # Prevent a message containing </script> from ending the data element early.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    (output_dir / "app.js").write_text(
        f"window.__ARCHIVE_DATA__ = {payload};\n\n{app_script}\n",
        encoding="utf-8",
        newline="\n",
    )
    title = str(archive["metadata"]["title"])
    html = template_text.replace("{{ARCHIVE_TITLE}}", title.replace("&", "&amp;").replace("<", "&lt;"))
    html = html.replace("{{ARCHIVE_JSON}}", payload)
    (output_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    write_json(output_dir / "manifest.json", _build_manifest(archive, output_dir, viewer_assets))
    return missing
