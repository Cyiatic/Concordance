from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import re
import secrets
import shutil
import sysconfig
import struct
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


SCHEMA_VERSION = 1
_SCHEMA_MIGRATION_PATHS = {0: (0, 1), 1: (1,)}
CAPTURE_SESSION_VERSION = 1
CAPTURE_SESSION_TYPE = "discord_visible_capture_session"
CAPTURE_EVIDENCE_VERSION = 1
EVIDENCE_VERSION = 1
# Stable schema identifier retained for compatibility with version-1 evidence.
EVIDENCE_TYPE = "discord_archive_evidence"
_MISSING = object()
_TIMESTAMP_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$", re.IGNORECASE)
_ALLOWED_REMOTE_MEDIA_HOSTS = frozenset({
    "cdn.discordapp.com",
    "images-ext-1.discordapp.net",
    "media.discordapp.net",
    "i.ytimg.com",
    "img.youtube.com",
})
_MAX_REMOTE_MEDIA_BYTES = 25 * 1024 * 1024
_MAX_EVIDENCE_ATTACHMENT_BYTES = 50 * 1024 * 1024


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
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _youtube_video_id(value: Any) -> str | None:
    if not _is_http_url(value):
        return None
    parsed = urlparse(str(value).strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    candidate = None
    if hostname in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path.lower() == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.lower().startswith(("/shorts/", "/embed/", "/live/")):
            candidate = parsed.path.strip("/").split("/", 1)[1]
    if not isinstance(candidate, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", candidate):
        return None
    return candidate


def _youtube_thumbnail_url(value: Any) -> str | None:
    video_id = _youtube_video_id(value)
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None


def _youtube_thumbnail_url_from_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    for candidate in re.findall(r"https?://[^\s<>]+", value):
        thumbnail = _youtube_thumbnail_url(candidate.rstrip(".,;:!?)]}"))
        if thumbnail:
            return thumbnail
    return None


def _remote_media_allowed(value: Any) -> bool:
    if not _is_http_url(value):
        return False
    try:
        parsed = urlparse(str(value).strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
            return False
    except ValueError:
        return False
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


class _ApprovedRemoteMediaRedirectHandler(HTTPRedirectHandler):
    """Prevent an approved media URL from redirecting to an unapproved host."""

    def redirect_request(self, request, file, code, message, headers, new_url):
        if not _remote_media_allowed(new_url):
            raise ValueError("Remote media redirect targets an unapproved host")
        return super().redirect_request(request, file, code, message, headers, new_url)


def _download_remote_media(url: str, destination: Path, max_bytes: int = _MAX_REMOTE_MEDIA_BYTES) -> int:
    if not _remote_media_allowed(url):
        raise ValueError(
            "Remote media download is limited to approved Discord CDN/proxy and YouTube thumbnail hosts"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    request = Request(url, headers={"User-Agent": "Concordance/1.0"})
    opener = build_opener(_ApprovedRemoteMediaRedirectHandler())
    total = 0
    try:
        with opener.open(request, timeout=30) as response, temporary.open("wb") as handle:
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


def _validate_media_reference(value: Any, field: str, errors: list[str]) -> None:
    """Validate a sticker/custom-emoji media reference without requiring a URL."""

    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    name = value.get("name")
    identifier = value.get("id")
    if not (isinstance(name, str) and name.strip()) and not (isinstance(identifier, str) and identifier.strip()):
        errors.append(f"{field} needs a non-empty name or id")
    for key in ("id", "name", "mime", "format"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")
    for key in ("path", "preview_path"):
        if key in value:
            _validate_local_reference(value[key], f"{field}.{key}", errors)
    if "url" in value and value["url"] is not None and not _is_http_url(value["url"]):
        errors.append(f"{field}.url must be an HTTP(S) URL or null")
    if "size_bytes" in value and value["size_bytes"] is not None:
        size = value["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            errors.append(f"{field}.size_bytes must be a non-negative integer or null")
    if "animated" in value and not isinstance(value["animated"], bool):
        errors.append(f"{field}.animated must be boolean")


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


def _validate_call(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    if value.get("type") != "voice":
        errors.append(f"{field}.type must be 'voice'")
    status = value.get("status")
    if status not in {"completed", "missed"}:
        errors.append(f"{field}.status must be 'completed' or 'missed'")
    for key in ("duration_label", "initiator_name"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")
    if not isinstance(value.get("duration_label"), str) or not value["duration_label"].strip():
        errors.append(f"{field}.duration_label must be a non-empty string")


def _validate_embed(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    for key in ("title", "description", "footer"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")
    for key in ("type", "site_name", "provider", "thumbnail_source"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")
    if "url" in value and value["url"] is not None and not _is_http_url(value["url"]):
        errors.append(f"{field}.url must be an HTTP(S) URL or null")
    for media_name in ("image", "thumbnail", "video", "audio"):
        url_key = f"{media_name}_url"
        path_key = f"{media_name}_path"
        if url_key in value and value[url_key] is not None and not _is_http_url(value[url_key]):
            errors.append(f"{field}.{url_key} must be an HTTP(S) URL or null")
        if path_key in value:
            _validate_local_reference(value[path_key], f"{field}.{path_key}", errors)
    if "local_path" in value:
        _validate_local_reference(value["local_path"], f"{field}.local_path", errors)


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


def _validate_profile(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return
    for key in (
        "presence",
        "pronouns",
        "member_since",
        "source",
        "custom_status",
        "banner_path",
        "avatar_decoration_path",
    ):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            errors.append(f"{field}.{key} must be a string or null")
    for key in ("banner_ref", "avatar_decoration_ref"):
        if key in value and value[key] is not None and not _is_http_url(value[key]):
            errors.append(f"{field}.{key} must be an HTTP(S) URL or null")
    for key in ("banner_path", "avatar_decoration_path"):
        if key in value:
            _validate_local_reference(value[key], f"{field}.{key}", errors)
    if "captured_at" in value and value["captured_at"] is not None:
        _validate_timestamp(value["captured_at"], f"{field}.captured_at", errors)
    activities = value.get("activities")
    if activities is not None:
        if not isinstance(activities, list) or any(not isinstance(item, str) for item in activities):
            errors.append(f"{field}.activities must be an array of strings")
    badges = value.get("badges")
    if badges is not None:
        if not isinstance(badges, list):
            errors.append(f"{field}.badges must be an array")
        else:
            for index, badge in enumerate(badges):
                badge_field = f"{field}.badges[{index}]"
                if not isinstance(badge, dict):
                    errors.append(f"{badge_field} must be an object")
                    continue
                for key in ("label", "detail"):
                    if key in badge and badge[key] is not None and not isinstance(badge[key], str):
                        errors.append(f"{badge_field}.{key} must be a string or null")
                if not (isinstance(badge.get("label"), str) and badge["label"].strip()) and not (
                    isinstance(badge.get("detail"), str) and badge["detail"].strip()
                ):
                    errors.append(f"{badge_field} needs a non-empty label or detail")
                if "icon_path" in badge:
                    _validate_local_reference(badge["icon_path"], f"{badge_field}.icon_path", errors)
                if "icon_ref" in badge and badge["icon_ref"] is not None and not _is_http_url(badge["icon_ref"]):
                    errors.append(f"{badge_field}.icon_ref must be an HTTP(S) URL or null")
    mutual_friends = value.get("mutual_friends")
    if mutual_friends is not None:
        if not isinstance(mutual_friends, list):
            errors.append(f"{field}.mutual_friends must be an array")
        else:
            for index, friend in enumerate(mutual_friends):
                friend_field = f"{field}.mutual_friends[{index}]"
                if not isinstance(friend, dict):
                    errors.append(f"{friend_field} must be an object")
                    continue
                for key in ("id", "display_name", "username", "avatar_alt"):
                    if key in friend and friend[key] is not None and not isinstance(friend[key], str):
                        errors.append(f"{friend_field}.{key} must be a string or null")
                if "avatar_path" in friend:
                    _validate_local_reference(friend["avatar_path"], f"{friend_field}.avatar_path", errors)
                if "avatar_ref" in friend and friend["avatar_ref"] is not None and not _is_http_url(friend["avatar_ref"]):
                    errors.append(f"{friend_field}.avatar_ref must be an HTTP(S) URL or null")


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
            if "profile" in participant and participant["profile"] is not None:
                _validate_profile(participant["profile"], f"participants[{index}].profile", errors)
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
        for media_field in ("stickers", "custom_emojis"):
            if media_field in message and not isinstance(message[media_field], list):
                errors.append(f"messages[{index}].{media_field} must be an array")
            elif isinstance(message.get(media_field), list):
                for media_index, media in enumerate(message[media_field]):
                    _validate_media_reference(media, f"messages[{index}].{media_field}[{media_index}]", errors)
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
        if "call" in message and message["call"] is not None:
            _validate_call(message["call"], f"messages[{index}].call", errors)
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


def _normalise_media_reference(value: Any, default_name: str, default_mime: str = "image/png") -> dict[str, Any]:
    """Normalize a sticker or custom emoji while retaining optional CDN media."""

    if isinstance(value, str):
        raw_id = None
        raw_name = None
        raw_reference: Any = value
        raw_preview_path = None
        raw_mime = default_mime
        raw_format = None
        raw_animated = None
    elif isinstance(value, dict):
        raw_id = _first(value, "id", "ID", "sticker_id", "stickerId", "emoji_id", "emojiId", default=None)
        raw_name = _first(value, "name", "Name", "label", "title", default=None)
        raw_reference = _first(
            value,
            "path",
            "local_path",
            "url",
            "image_url",
            "imageUrl",
            "asset_url",
            "assetUrl",
            "src",
            "source",
            default=None,
        )
        raw_preview_path = _first(value, "preview_path", "previewPath", default=None)
        raw_mime = _first(value, "mime", "content_type", "contentType", default=None) or default_mime
        raw_format = _first(value, "format", "format_type", "formatType", default=None)
        raw_animated = _first(value, "animated", "is_animated", "isAnimated", default=_MISSING)
    else:
        raw_id = None
        raw_name = None
        raw_reference = None
        raw_preview_path = None
        raw_mime = default_mime
        raw_format = None
        raw_animated = None

    if isinstance(raw_reference, dict):
        raw_reference = _first(raw_reference, "path", "local_path", "url", "image_url", "proxy_url", default=None)
    local_path = _normalise_local_reference(raw_reference)
    item: dict[str, Any] = {
        "name": str(raw_name or raw_id or default_name),
        "mime": str(raw_mime or default_mime),
    }
    if raw_id is not None and str(raw_id).strip():
        item["id"] = str(raw_id).strip()
    if local_path:
        item["path"] = local_path
    elif _is_http_url(raw_reference):
        item["url"] = str(raw_reference).strip()
    preview_path = _normalise_local_reference(raw_preview_path)
    if preview_path:
        item["preview_path"] = preview_path
    if raw_format is not None and str(raw_format).strip():
        item["format"] = str(raw_format).strip()
    if isinstance(raw_animated, bool):
        item["animated"] = raw_animated
    return item


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


def _normalise_call(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    call_type = _first(value, "type", "kind", "call_type", "callType", default="voice")
    status = _first(value, "status", "state", default="completed")
    duration = _first(value, "duration_label", "duration", "durationLabel", default=None)
    initiator = _first(value, "initiator_name", "initiator", "caller", "author_name", default=None)
    if not isinstance(call_type, str) or not call_type.strip():
        call_type = "voice"
    if not isinstance(status, str) or not status.strip():
        status = "completed"
    if not isinstance(duration, str) or not duration.strip():
        return None
    normalized: dict[str, Any] = {
        "type": call_type.strip().lower(),
        "status": status.strip().lower(),
        "duration_label": duration.strip(),
    }
    if isinstance(initiator, str) and initiator.strip():
        normalized["initiator_name"] = initiator.strip()
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

    for media_name, input_keys in (
        ("image", ("image_path", "image", "Image", "image_url", "imageUrl", "Image URL")),
        ("thumbnail", ("thumbnail_path", "thumbnail", "Thumbnail", "thumbnail_url", "thumbnailUrl")),
        ("video", ("video_path", "video", "Video", "video_url", "videoUrl")),
        ("audio", ("audio_path", "audio", "Audio", "audio_url", "audioUrl")),
    ):
        media = _first(value, *input_keys, default=None)
        if isinstance(media, dict):
            media = _first(media, "path", "local_path", "url", "image_url", "video_url", "audio_url", "proxy_url", default=None)
        local_media = _normalise_local_reference(media)
        if local_media:
            normalized[f"{media_name}_path"] = local_media
        elif _is_http_url(media):
            normalized[f"{media_name}_url"] = media.strip()
    for key in ("type", "site_name", "provider", "thumbnail_source"):
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
    raw_stickers = _first(record, "stickers", "Stickers", "sticker_items", "Sticker Items", "stickerItems", default=[])
    if raw_stickers is None:
        raw_stickers = []
    elif not isinstance(raw_stickers, list):
        raw_stickers = [raw_stickers]
    raw_custom_emojis = _first(
        record,
        "custom_emojis",
        "customEmojis",
        "Custom Emojis",
        "emoji_assets",
        "emojiAssets",
        default=[],
    )
    if raw_custom_emojis is None:
        raw_custom_emojis = []
    elif not isinstance(raw_custom_emojis, list):
        raw_custom_emojis = [raw_custom_emojis]
    raw_call = _first(record, "call", "Call", "call_event", "callEvent", default=None)
    message: dict[str, Any] = {
        "id": str(message_id),
        "author_id": "me",
        "timestamp": timestamp,
        "content": content,
        "channel_id": channel_id,
        "attachments": [_normalise_attachment(item) for item in raw_attachments],
        "reactions": [_normalise_reaction(item) for item in raw_reactions],
        "embeds": [_normalise_embed(item) for item in raw_embeds],
        "stickers": [_normalise_media_reference(item, "sticker") for item in raw_stickers],
        "custom_emojis": [_normalise_media_reference(item, "custom emoji") for item in raw_custom_emojis],
    }
    normalized_call = _normalise_call(raw_call)
    if normalized_call is not None:
        message["call"] = normalized_call
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


def _normalise_expected_dates(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("expected checkpoint dates must be an array of YYYY-MM-DD values")
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("expected checkpoint dates must be non-empty YYYY-MM-DD values")
        candidate = value.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            raise ValueError(f"expected checkpoint date must use YYYY-MM-DD: {candidate!r}")
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
        except ValueError as error:
            raise ValueError(f"expected checkpoint date must use YYYY-MM-DD: {candidate!r}") from error
        normalized.add(candidate)
    return sorted(normalized)


def _observed_message_dates(record: dict[str, Any]) -> set[str]:
    dates: set[str] = set()
    timestamp = _first(record, "timestamp", "Timestamp", "created_at", "createdAt", "Date", default=None)
    parsed_timestamp = parse_timestamp(timestamp)
    if parsed_timestamp is not None:
        dates.add(parsed_timestamp.date().isoformat())

    source_display = record.get("source_display")
    if not isinstance(source_display, dict):
        return dates
    display_values = [source_display.get("date"), source_display.get("label")]
    for value in display_values:
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = value.strip()
        iso_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", candidate)
        if iso_match:
            try:
                dates.add(datetime.strptime(iso_match.group(0), "%Y-%m-%d").date().isoformat())
            except ValueError:
                pass
        calendar_match = re.search(r"\b[A-Za-z]+\s+\d{1,2},\s+\d{4}\b", candidate)
        if calendar_match:
            try:
                dates.add(datetime.strptime(calendar_match.group(0), "%B %d, %Y").date().isoformat())
            except ValueError:
                pass
    return dates


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
    observed_dates = {
        date
        for record in records
        for date in _observed_message_dates(record)
    }
    return {
        "source_file": source_file,
        "message_count": len(records),
        "message_ids": {message_id for message_id in (_raw_message_id(record, index) for index, record in enumerate(records)) if message_id},
        "observed_dates": observed_dates,
        "oldest_message_id": capture_range.get("oldest_message_id") or first_id,
        "oldest_timestamp": capture_range.get("oldest_timestamp") or first_timestamp,
        "newest_message_id": capture_range.get("newest_message_id") or last_id,
        "newest_timestamp": capture_range.get("newest_timestamp") or last_timestamp,
        "at_start": bool(capture_range.get("at_start")),
        "at_end": bool(capture_range.get("at_end")),
        "has_capture_range": bool(capture_range),
        "scroll_top": capture_range.get("scroll_top") if isinstance(capture_range.get("scroll_top"), (int, float)) and not isinstance(capture_range.get("scroll_top"), bool) else None,
        "scroll_height": capture_range.get("scroll_height") if isinstance(capture_range.get("scroll_height"), (int, float)) and not isinstance(capture_range.get("scroll_height"), bool) else None,
        "viewport_height": capture_range.get("viewport_height") if isinstance(capture_range.get("viewport_height"), (int, float)) and not isinstance(capture_range.get("viewport_height"), bool) else None,
        "requested_direction": capture_range.get("requested_direction") if capture_range.get("requested_direction") in {"none", "older", "newer"} else "none",
        "moved_pixels": capture_range.get("moved_pixels") if isinstance(capture_range.get("moved_pixels"), (int, float)) and not isinstance(capture_range.get("moved_pixels"), bool) else None,
        "previous_scroll_top": capture_range.get("previous_scroll_top") if isinstance(capture_range.get("previous_scroll_top"), (int, float)) and not isinstance(capture_range.get("previous_scroll_top"), bool) else None,
    }


def _coverage_report(
    ranges: list[dict[str, Any]],
    duplicate_count: int = 0,
    conflict_count: int = 0,
    reached_start: bool = False,
    reached_end: bool = False,
    expected_dates: list[str] | None = None,
) -> dict[str, Any]:
    expected_dates = _normalise_expected_dates(expected_dates)
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

    observed_dates = {
        date
        for item in ordered
        for date in item.get("observed_dates", set())
        if isinstance(date, str)
    }
    checkpoints = [
        {
            "date": date,
            "observed": date in observed_dates,
            "range_count": sum(
                1
                for item in ordered
                if date in item.get("observed_dates", set())
            ),
        }
        for date in expected_dates
    ]
    missing_expected_dates = [checkpoint["date"] for checkpoint in checkpoints if not checkpoint["observed"]]
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
    checkpoints_complete = not missing_expected_dates
    if start_confirmed and end_confirmed and linked and conflict_count == 0 and checkpoints_complete:
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
    if missing_expected_dates:
        dates = ", ".join(missing_expected_dates)
        notes.append(f"Expected checkpoint date(s) were not observed in the captured messages: {dates}.")
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
    elif missing_expected_dates:
        dates = ", ".join(missing_expected_dates)
        next_action = f"Return to the open DM and capture a rendered range containing these expected date checkpoints: {dates}. Then add it and merge again."
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
        "expected_dates": expected_dates,
        "checkpoints": checkpoints,
        "missing_expected_dates": missing_expected_dates,
        "ranges": public_ranges,
        "notes": notes,
        "next_action": next_action,
    }


def merge_transcripts(
    input_paths: list[Path],
    output_path: Path,
    reached_start: bool = False,
    reached_end: bool = False,
    expected_dates: list[str] | None = None,
) -> dict[str, Any]:
    """Merge overlapping, user-captured transcript ranges into one transcript.

    Inputs are expected to be captures from the same open DM and stored beside
    the output so relative local asset references remain portable. Message IDs
    deduplicate overlapping ranges; conflicting duplicates are retained once and
    reported in coverage metadata rather than silently overwritten.
    """

    if not input_paths:
        raise ValueError("At least one transcript capture is required")
    expected_dates = _normalise_expected_dates(expected_dates)
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
                    # Discord can lazy-load link-preview thumbnails between
                    # overlapping captures. The stable embed identity is the
                    # link and its text; the thumbnail is an enrichment that
                    # should not turn an otherwise identical message into a
                    # conflicting duplicate.
                    if collection == "embeds":
                        normalized_value.pop(url_key, None)
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
            for incoming_index, incoming_value in enumerate(incoming_values):
                if not isinstance(incoming_value, dict) or not _remote_media_allowed(incoming_value.get(url_key)):
                    if collection != "embeds" or not isinstance(incoming_value, dict):
                        continue
                match = None
                if collection == "embeds":
                    # Match a lazy-loaded embed by stable presentation fields,
                    # falling back to its position when Discord omitted those
                    # fields from one render.
                    stable_keys = ("url", "title", "site_name", "description")
                    incoming_identity = {
                        key: incoming_value.get(key)
                        for key in stable_keys
                        if incoming_value.get(key) not in (None, "")
                    }
                    for existing_value in existing_values:
                        if not isinstance(existing_value, dict):
                            continue
                        existing_identity = {
                            key: existing_value.get(key)
                            for key in stable_keys
                            if existing_value.get(key) not in (None, "")
                        }
                        if incoming_identity and existing_identity == incoming_identity:
                            match = existing_value
                            break
                    if match is None and incoming_index < len(existing_values) and isinstance(existing_values[incoming_index], dict):
                        match = existing_values[incoming_index]
                elif _remote_media_allowed(incoming_value.get(url_key)):
                    incoming_identity = _canonical_remote_reference(incoming_value[url_key])
                    for existing_value in existing_values:
                        if not isinstance(existing_value, dict):
                            continue
                        if _canonical_remote_reference(existing_value.get(url_key)) == incoming_identity:
                            match = existing_value
                            break
                if match is None:
                    continue
                for key, value in incoming_value.items():
                    if match.get(key) in (None, "") and value not in (None, ""):
                        match[key] = value
                if _remote_media_allowed(incoming_value.get(url_key)):
                    match[url_key] = incoming_value[url_key]

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
        expected_dates=expected_dates,
    )
    merged_metadata["coverage"] = coverage
    merged = {
        "metadata": merged_metadata,
        "participants": list(participants_by_id.values()),
        "messages": [messages_by_id[key] for key in message_order],
    }
    merged_metadata["capture_diagnostics"] = _capture_media_summary(merged)
    write_json(output_path, merged)
    return {
        "messages": len(merged["messages"]),
        "participants": len(merged["participants"]),
        "duplicates": duplicate_count,
        "conflicts": conflict_count,
        "coverage": coverage,
    }


def _load_capture_session(session_path: Path) -> dict[str, Any]:
    session_path = session_path.resolve()
    session = load_json(session_path.resolve())
    if session.get("session_version") != CAPTURE_SESSION_VERSION:
        raise ValueError(
            f"capture session version must be {CAPTURE_SESSION_VERSION}; "
            f"got {session.get('session_version')!r}"
        )
    if session.get("type") != CAPTURE_SESSION_TYPE:
        raise ValueError(f"capture session type must be {CAPTURE_SESSION_TYPE!r}")
    captures = session.get("captures")
    if not isinstance(captures, list):
        raise ValueError("capture session captures must be an array")
    expected_dates = session.get("expected_dates", [])
    if not isinstance(expected_dates, list):
        raise ValueError("capture session expected_dates must be an array")
    _normalise_expected_dates(expected_dates)
    seen_paths: set[str] = set()
    for index, capture in enumerate(captures):
        if not isinstance(capture, dict):
            raise ValueError(f"capture session capture {index + 1} must be an object")
        relative = _normalise_local_reference(capture.get("path"))
        if relative is None:
            raise ValueError(f"capture session capture {index + 1} has an unsafe or missing path")
        if relative in seen_paths:
            raise ValueError(f"capture session lists capture more than once: {relative}")
        seen_paths.add(relative)
        evidence = capture.get("evidence", [])
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list):
            raise ValueError(f"capture session evidence must be an array: {relative}")
        evidence_paths: set[str] = set()
        for evidence_index, record in enumerate(evidence):
            if not isinstance(record, dict):
                raise ValueError(f"capture session evidence {evidence_index + 1} must be an object: {relative}")
            kind = record.get("kind")
            if kind not in {"dom", "screenshots"}:
                raise ValueError(f"capture session evidence kind must be dom or screenshots: {relative}")
            evidence_relative = _normalise_local_reference(record.get("path"))
            if evidence_relative is None:
                raise ValueError(f"capture session evidence path is unsafe or missing: {relative}")
            if evidence_relative in evidence_paths:
                raise ValueError(f"capture session evidence is listed more than once: {evidence_relative}")
            evidence_paths.add(evidence_relative)
            evidence_path = (session_path.parent / Path(evidence_relative)).resolve()
            try:
                evidence_path.relative_to(session_path.parent)
            except ValueError as error:
                raise ValueError(f"capture session evidence escapes its directory: {evidence_relative}") from error
            if not evidence_path.is_file():
                raise FileNotFoundError(f"capture evidence file does not exist: {evidence_relative}")
            size_bytes = record.get("size_bytes")
            if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
                raise ValueError(f"capture session evidence has an invalid size: {evidence_relative}")
            if evidence_path.stat().st_size != size_bytes:
                raise ValueError(f"capture evidence changed after it was attached: {evidence_relative}")
            digest = record.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError(f"capture session evidence has an invalid SHA-256: {evidence_relative}")
            if _sha256(evidence_path).lower() != digest.lower():
                raise ValueError(f"capture evidence hash mismatch: {evidence_relative}")
    return session


def _session_capture_paths(session_path: Path, session: dict[str, Any]) -> list[Path]:
    session_path = session_path.resolve()
    paths: list[Path] = []
    for capture in session.get("captures", []):
        relative = _normalise_local_reference(capture.get("path"))
        if relative is None:
            raise ValueError("capture session contains an unsafe or missing capture path")
        capture_path = (session_path.parent / Path(relative)).resolve()
        if capture_path == session_path:
            raise ValueError("capture session cannot use itself as a capture")
        if capture_path.parent != session_path.parent:
            raise ValueError("capture session captures and session file must be in the same directory")
        try:
            capture_path.relative_to(session_path.parent)
        except ValueError as error:
            raise ValueError("capture session contains a capture outside its session directory") from error
        if not capture_path.is_file():
            raise FileNotFoundError(f"capture file does not exist: {relative}")
        expected_hash = capture.get("sha256")
        if expected_hash is not None:
            if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
                raise ValueError(f"capture session has an invalid SHA-256 for: {relative}")
            if _sha256(capture_path).lower() != expected_hash.lower():
                raise ValueError(f"capture changed after it was added to the session: {relative}")
        paths.append(capture_path)
    return paths


def _empty_capture_session_coverage(expected_dates: list[str] | None = None) -> dict[str, Any]:
    expected_dates = _normalise_expected_dates(expected_dates)
    return {
        "version": 1,
        "status": "unverified",
        "complete": False,
        "range_count": 0,
        "unique_message_count": 0,
        "duplicate_message_count": 0,
        "conflict_count": 0,
        "start_confirmed": False,
        "end_confirmed": False,
        "ranges_linked": False,
        "unlinked_ranges": [],
        "expected_dates": expected_dates,
        "checkpoints": [
            {"date": date, "observed": False, "range_count": 0}
            for date in expected_dates
        ],
        "missing_expected_dates": expected_dates,
        "ranges": [],
        "notes": ["No capture ranges have been added yet."],
        "next_action": (
            "Return to the open DM, capture the first rendered range, then add it to this session."
            if not expected_dates
            else "Return to the open DM, capture the first rendered range, then add it to this session; expected date checkpoints will be checked as ranges are added."
        ),
    }


def _capture_session_coverage(session_path: Path, session: dict[str, Any]) -> dict[str, Any]:
    expected_dates = _normalise_expected_dates(session.get("expected_dates", []))
    paths = _session_capture_paths(session_path, session)
    if not paths:
        return _empty_capture_session_coverage(expected_dates)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=session_path.resolve().parent,
            prefix=f".{session_path.stem}.",
            suffix=".status.json",
        ) as handle:
            temporary_path = Path(handle.name)
        summary = merge_transcripts(
            paths,
            temporary_path,
            reached_start=bool(session.get("reached_start")),
            reached_end=bool(session.get("reached_end")),
            expected_dates=expected_dates,
        )
        return summary["coverage"]
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _empty_capture_media_summary() -> dict[str, Any]:
    return {
        "message_count": 0,
        "attachments": 0,
        "embeds": 0,
        "embed_media": 0,
        "stickers": 0,
        "custom_emojis": 0,
        "reactions": 0,
        "replies": 0,
        "calls": 0,
        "profile_media": 0,
        "local_media": 0,
        "remote_media": 0,
        "allowed_remote_media": 0,
        "unapproved_remote_media": 0,
        "remote_media_hosts": {},
    }


def _capture_media_summary(value: dict[str, Any]) -> dict[str, Any]:
    """Summarize captured media/features without copying message content."""

    summary = _empty_capture_media_summary()
    messages = _transcript_records(value)
    summary["message_count"] = len(messages)
    hosts: dict[str, int] = {}

    def reference(local_value: Any = None, remote_value: Any = None, profile: bool = False) -> None:
        if isinstance(local_value, str) and _normalise_local_reference(local_value):
            summary["local_media"] += 1
            if profile:
                summary["profile_media"] += 1
        if not _is_http_url(remote_value):
            return
        summary["remote_media"] += 1
        if profile:
            summary["profile_media"] += 1
        hostname = (urlparse(str(remote_value).strip()).hostname or "").lower().rstrip(".")
        if hostname:
            hosts[hostname] = hosts.get(hostname, 0) + 1
        if _remote_media_allowed(remote_value):
            summary["allowed_remote_media"] += 1
        else:
            summary["unapproved_remote_media"] += 1

    participants = value.get("participants", [])
    if isinstance(participants, list):
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            reference(participant.get("avatar_path"), participant.get("avatar_ref"), profile=True)
            profile = participant.get("profile")
            if not isinstance(profile, dict):
                continue
            reference(profile.get("banner_path"), profile.get("banner_ref"), profile=True)
            reference(profile.get("avatar_decoration_path"), profile.get("avatar_decoration_ref"), profile=True)
            badges = profile.get("badges", [])
            if isinstance(badges, list):
                for badge in badges:
                    if isinstance(badge, dict):
                        reference(badge.get("icon_path"), badge.get("icon_ref"), profile=True)
            friends = profile.get("mutual_friends", [])
            if isinstance(friends, list):
                for friend in friends:
                    if isinstance(friend, dict):
                        reference(friend.get("avatar_path"), friend.get("avatar_ref"), profile=True)

    for message in messages:
        if not isinstance(message, dict):
            continue
        attachments = message.get("attachments", [])
        if isinstance(attachments, list):
            summary["attachments"] += len(attachments)
            for attachment in attachments:
                if isinstance(attachment, dict):
                    reference(attachment.get("path") or attachment.get("local_path"), attachment.get("url"))
        embeds = message.get("embeds", [])
        if isinstance(embeds, list):
            summary["embeds"] += len(embeds)
            for embed in embeds:
                if not isinstance(embed, dict):
                    continue
                for media_name in ("image", "thumbnail", "video", "audio"):
                    path = embed.get(f"{media_name}_path") or embed.get("local_path")
                    url = embed.get(f"{media_name}_url")
                    if path or url:
                        summary["embed_media"] += 1
                    reference(path, url)
        for collection_name in ("stickers", "custom_emojis"):
            collection = message.get(collection_name, [])
            if not isinstance(collection, list):
                continue
            summary[collection_name] += len(collection)
            for media in collection:
                if isinstance(media, dict):
                    reference(media.get("path") or media.get("preview_path"), media.get("url"))
        reactions = message.get("reactions", [])
        if isinstance(reactions, list):
            summary["reactions"] += len(reactions)
        if message.get("reply_to"):
            summary["replies"] += 1
        if isinstance(message.get("call"), dict):
            summary["calls"] += 1

    summary["remote_media_hosts"] = dict(sorted(hosts.items()))
    return summary


def _capture_session_media(session_path: Path, session: dict[str, Any]) -> dict[str, Any]:
    total = _empty_capture_media_summary()
    hosts: dict[str, int] = {}
    for capture_path in _session_capture_paths(session_path, session):
        summary = _capture_media_summary(load_json(capture_path))
        for key in total:
            if key == "remote_media_hosts":
                continue
            total[key] += int(summary.get(key) or 0)
        for host, count in summary.get("remote_media_hosts", {}).items():
            hosts[host] = hosts.get(host, 0) + int(count or 0)
    total["remote_media_hosts"] = dict(sorted(hosts.items()))
    return total


def _capture_session_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def init_capture_session(
    output_path: Path,
    channel_id: str | None = None,
    title: str | None = None,
    expected_dates: list[str] | None = None,
) -> dict[str, Any]:
    """Create an empty manifest for a user-guided visible-DOM capture session."""

    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"capture session already exists: {output_path}")
    expected_dates = _normalise_expected_dates(expected_dates)
    session = {
        "session_version": CAPTURE_SESSION_VERSION,
        "type": CAPTURE_SESSION_TYPE,
        "created_at": _capture_session_timestamp(),
        "title": title.strip() if isinstance(title, str) and title.strip() else None,
        "channel_id": channel_id.strip() if isinstance(channel_id, str) and channel_id.strip() else None,
        "reached_start": False,
        "reached_end": False,
        "expected_dates": expected_dates,
        "captures": [],
    }
    write_json(output_path, session)
    return session


def add_capture_to_session(session_path: Path, capture_path: Path) -> dict[str, Any]:
    """Record one attended capture range and refresh the session coverage report."""

    session_path = session_path.resolve()
    session = _load_capture_session(session_path)
    capture_path = capture_path.resolve()
    if not capture_path.is_file():
        raise FileNotFoundError(f"capture file does not exist: {capture_path}")
    if capture_path.parent != session_path.parent:
        raise ValueError("capture session captures and session file must be in the same directory")
    if capture_path == session_path:
        raise ValueError("capture session cannot use itself as a capture")
    relative = capture_path.relative_to(session_path.parent).as_posix()
    relative = _normalise_local_reference(relative)
    if relative is None:
        raise ValueError("capture path must be a safe relative path")
    if any(capture.get("path") == relative for capture in session["captures"]):
        raise ValueError(f"capture session already contains: {relative}")

    value = load_json(capture_path)
    records = _transcript_records(value)
    if not records:
        raise ValueError(f"capture contains no messages: {relative}")
    metadata = _transcript_metadata(value)
    channel_id = metadata.get("channel_id")
    if not isinstance(channel_id, str) or not channel_id.strip():
        raise ValueError(f"capture is missing metadata.channel_id: {relative}")
    if not isinstance(metadata.get("capture_range"), dict):
        raise ValueError(
            f"capture is missing metadata.capture_range; run the visible-DOM adapter: {relative}"
        )
    channel_id = channel_id.strip()
    existing_channel_id = session.get("channel_id")
    if existing_channel_id and str(existing_channel_id) != channel_id:
        raise ValueError(
            f"capture channel ID {channel_id!r} does not match session channel ID {existing_channel_id!r}"
        )
    if not existing_channel_id:
        session["channel_id"] = channel_id
    if not session.get("title") and isinstance(metadata.get("title"), str) and metadata["title"].strip():
        session["title"] = metadata["title"].strip()

    summary = _capture_range_summary(relative, records, metadata)
    entry = {
        "path": relative,
        "sha256": _sha256(capture_path),
        "message_count": summary["message_count"],
        "oldest_message_id": summary["oldest_message_id"],
        "oldest_timestamp": summary["oldest_timestamp"],
        "newest_message_id": summary["newest_message_id"],
        "newest_timestamp": summary["newest_timestamp"],
        "at_start": summary["at_start"],
        "at_end": summary["at_end"],
        "has_capture_range": summary["has_capture_range"],
        "media": _capture_media_summary(value),
    }
    for key in (
        "scroll_top",
        "scroll_height",
        "viewport_height",
        "moved_pixels",
        "previous_scroll_top",
    ):
        if summary.get(key) is not None:
            entry[key] = summary[key]
    entry["requested_direction"] = summary.get("requested_direction", "none")
    session["captures"].append(entry)
    write_json(session_path, session)
    coverage = _capture_session_coverage(session_path, session)
    return {"capture": entry, "coverage": coverage}


def attach_capture_evidence(
    session_path: Path,
    capture_path: Path,
    dom_paths: list[Path] | None = None,
    screenshot_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Attach explicitly supplied rendered evidence to one tracked range.

    The browser adapter remains read-only. The operator supplies files saved by
    the attended browser step, and this function copies them into the private
    session directory, records hashes beside the matching range, and makes the
    relationship verifiable later.
    """

    session_path = session_path.resolve()
    session = _load_capture_session(session_path)
    capture_path = capture_path.resolve()
    if capture_path.parent != session_path.parent:
        raise ValueError("capture evidence and session files must be in the same directory")
    relative = _normalise_local_reference(capture_path.relative_to(session_path.parent).as_posix())
    if relative is None:
        raise ValueError("capture path must be a safe relative path")
    _session_capture_paths(session_path, session)
    capture = next((item for item in session["captures"] if item.get("path") == relative), None)
    if capture is None:
        raise ValueError(f"capture session does not contain: {relative}")

    records = _copy_evidence_inputs(dom_paths, session_path.parent, "dom") + _copy_evidence_inputs(
        screenshot_paths,
        session_path.parent,
        "screenshots",
    )
    existing = capture.get("evidence", [])
    if existing is None:
        existing = []
    existing_by_path = {
        item.get("path"): item
        for item in existing
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for record in records:
        existing_by_path[record["path"]] = {
            "version": CAPTURE_EVIDENCE_VERSION,
            **record,
        }
    capture["evidence"] = [existing_by_path[key] for key in sorted(existing_by_path)]
    write_json(session_path, session)
    status = capture_session_status(session_path)
    updated_capture = next(item for item in status["captures"] if item.get("path") == relative)
    return {
        "session": status,
        "capture": updated_capture,
        "attached": records,
    }


def _capture_session_evidence_summary(status: dict[str, Any]) -> dict[str, int]:
    summary = {"files": 0, "dom": 0, "screenshots": 0, "bytes": 0}
    captures = status.get("captures", []) if isinstance(status.get("captures"), list) else []
    for capture in captures:
        if not isinstance(capture, dict):
            continue
        evidence = capture.get("evidence", [])
        if not isinstance(evidence, list):
            continue
        for record in evidence:
            if not isinstance(record, dict):
                continue
            summary["files"] += 1
            kind = record.get("kind")
            if kind in {"dom", "screenshots"}:
                summary[kind] += 1
            size_bytes = record.get("size_bytes")
            if isinstance(size_bytes, int) and not isinstance(size_bytes, bool) and size_bytes >= 0:
                summary["bytes"] += size_bytes
    return summary


def _capture_session_step_plan(status: dict[str, Any]) -> dict[str, Any]:
    """Create a bounded, message-free next-step plan for an attended session."""

    coverage = status.get("coverage") if isinstance(status.get("coverage"), dict) else {}
    captures = [item for item in status.get("captures", []) if isinstance(item, dict)]
    missing_dates = [
        value
        for value in coverage.get("missing_expected_dates", [])
        if isinstance(value, str) and value.strip()
    ]
    start_confirmed = bool(coverage.get("start_confirmed") or status.get("reached_start"))
    end_confirmed = bool(coverage.get("end_confirmed") or status.get("reached_end"))

    direction = "none"
    kind = "capture_initial"
    if not captures:
        action = (
            "Capture the currently rendered range in the open DM. For a fresh chat, begin at the newest visible messages."
        )
        reason = "No rendered range has been added to this session yet."
    elif coverage.get("conflict_count"):
        kind = "repair_overlap"
        action = "Review the conflicting overlap records, then recapture that transition with matching rendered messages."
        reason = "Overlapping ranges contain records that disagree."
    elif coverage.get("unlinked_ranges"):
        kind = "repair_overlap"
        action = "Recapture the unlinked transition and include at least one message shared with the adjacent range."
        reason = "Adjacent ranges do not share a rendered message."
    elif not end_confirmed:
        kind = "capture_newer"
        direction = "newer"
        action = "Move toward the newest visible boundary, wait for Discord to settle, and capture an overlapping range."
        reason = "The newest boundary has not been confirmed."
    elif not start_confirmed:
        kind = "capture_older"
        direction = "older"
        action = "Move toward the oldest visible boundary, wait for Discord to settle, and capture an overlapping range."
        reason = "The oldest boundary has not been confirmed."
    elif missing_dates:
        kind = "capture_checkpoint"
        direction = "older"
        action = f"Capture a rendered range containing the expected checkpoint date(s): {', '.join(missing_dates)}."
        reason = "The session has not observed every configured date checkpoint."
    else:
        kind = "complete"
        action = "Coverage is verified from the rendered ranges. Build the archive and attach any final evidence before sharing."
        reason = "Both boundaries are confirmed, ranges overlap, and all checkpoints are observed."

    reference_capture: dict[str, Any] | None = None
    if captures and direction in {"older", "newer"}:
        key = "oldest_timestamp" if direction == "older" else "newest_timestamp"
        reference_capture = sorted(captures, key=lambda item: str(item.get(key) or ""))[0 if direction == "older" else -1]
    previous_scroll_top = None
    if reference_capture:
        value = reference_capture.get("scroll_top")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            previous_scroll_top = value

    adapter_options = {
        "direction": direction,
        "previous_scroll_top": previous_scroll_top,
        "settle_ms": 900,
        "overlap_messages": 1,
    }
    session_name = str(status.get("session") or "capture-session.json")
    copy_text = (
        "No further capture range is required. Build the archive, verify it, and attach any final evidence before sharing."
        if kind == "complete"
        else (
            f"Capture the next rendered range with options {json.dumps(adapter_options, separators=(',', ':'))}. "
            f"Then run: python -m concordance capture-session add --session {session_name} --input <capture.json>"
        )
    )
    return {
        "version": 1,
        "kind": kind,
        "direction": direction,
        "action": action,
        "reason": reason,
        "reference_capture": reference_capture.get("path") if reference_capture else None,
        "adapter_options": adapter_options,
        "overlap_required": 1,
        "copy_text": copy_text,
    }


def capture_session_status(session_path: Path) -> dict[str, Any]:
    """Return a private-data-safe snapshot of a guided capture session."""

    session_path = session_path.resolve()
    session = _load_capture_session(session_path)
    coverage = _capture_session_coverage(session_path, session)
    media = _capture_session_media(session_path, session)
    status = coverage["status"]
    snapshot = {
        "session_version": session["session_version"],
        "type": session["type"],
        "session": session_path.name,
        "title": session.get("title"),
        "channel_id": session.get("channel_id"),
        "capture_count": len(session["captures"]),
        "captures": session["captures"],
        "reached_start": bool(session.get("reached_start")),
        "reached_end": bool(session.get("reached_end")),
        "expected_dates": coverage.get("expected_dates", []),
        "finalized_archive": session.get("finalized_archive"),
        "coverage": coverage,
        "media": media,
        "evidence": _capture_session_evidence_summary({"captures": session["captures"]}),
        "status": status,
        "complete": coverage["complete"],
        "next_action": coverage["next_action"],
    }
    snapshot["next_step"] = _capture_session_step_plan(snapshot)
    return snapshot


def capture_session_next(session_path: Path) -> dict[str, Any]:
    """Return the next bounded browser/capture action without message content."""

    status = capture_session_status(session_path)
    return {
        "session": status["session"],
        "status": status["status"],
        "complete": status["complete"],
        "next_step": status["next_step"],
    }


def _capture_dashboard_template_path() -> Path:
    package_template = Path(__file__).resolve().parent / "viewer" / "capture_template.html"
    source_template = Path(__file__).resolve().parents[2] / "viewer" / "capture_template.html"
    installed_template = Path(sysconfig.get_path("data")) / "viewer" / "capture_template.html"
    for candidate in (package_template, source_template, installed_template):
        if candidate.is_file():
            return candidate
    return source_template


def _capture_dashboard_script_path(template_path: Path) -> Path:
    return template_path.with_name("capture_app.js")


def _build_capture_dashboard_manifest(output_dir: Path) -> dict[str, Any]:
    files = []
    for path in sorted(path for path in output_dir.rglob("*") if path.is_file() and path.name != "manifest.json"):
        files.append({
            "path": path.relative_to(output_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return {
        "manifest_version": 1,
        "capture_dashboard_version": 1,
        "files": files,
    }


def build_capture_session_dashboard(
    session_path: Path,
    output_dir: Path,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Build a local, message-free guide for an attended capture session."""

    session_path = session_path.resolve()
    if not session_path.is_file():
        raise FileNotFoundError(f"capture session does not exist: {session_path}")
    status = capture_session_status(session_path)
    template = template_path or _capture_dashboard_template_path()
    script_path = _capture_dashboard_script_path(template)
    if not template.is_file():
        raise FileNotFoundError(f"Capture dashboard template not found: {template}")
    if not script_path.is_file():
        raise FileNotFoundError(f"Capture dashboard script not found: {script_path}")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    viewer_assets = _copy_viewer_assets(_template_path(), output_dir)
    generated_at = _capture_session_timestamp()
    payload = {
        "dashboard_version": 1,
        "generated_at": generated_at,
        "session_file": session_path.name,
        "session_sha256": _sha256(session_path),
        "status": status,
    }
    write_json(output_dir / "capture-session.json", payload)

    template_text = template.read_text(encoding="utf-8")
    script = script_path.read_text(encoding="utf-8")
    if "{{CAPTURE_SESSION_TITLE}}" not in template_text:
        raise ValueError("Capture dashboard template is missing the CAPTURE_SESSION_TITLE placeholder")
    title = str(status.get("title") or "Guided capture session")
    escaped_title = title.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    html = template_text.replace("{{CAPTURE_SESSION_TITLE}}", escaped_title)
    (output_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    serialized = serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    (output_dir / "capture.js").write_text(
        f"window.__CONCORDANCE_CAPTURE_SESSION__ = {serialized};\n\n{script}\n",
        encoding="utf-8",
        newline="\n",
    )
    write_json(output_dir / "manifest.json", _build_capture_dashboard_manifest(output_dir))
    return {
        "output": output_dir,
        "session": session_path.name,
        "status": status["status"],
        "complete": status["complete"],
        "capture_count": status["capture_count"],
        "viewer_assets": viewer_assets,
    }


def verify_capture_session_dashboard(output_dir: Path) -> list[str]:
    """Verify a generated capture-session guide and its integrity manifest."""

    output_dir = output_dir.resolve()
    errors: list[str] = []
    if not output_dir.is_dir():
        return [f"capture dashboard directory does not exist: {output_dir}"]
    manifest_path = output_dir / "manifest.json"
    try:
        manifest = load_json(manifest_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"manifest.json could not be read: {error}"]
    if manifest.get("manifest_version") != 1:
        errors.append("manifest_version must be 1")
    if manifest.get("capture_dashboard_version") != 1:
        errors.append("capture_dashboard_version must be 1")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        errors.append("manifest.files must be an array")
        entries = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"manifest.files[{index}] must be an object")
            continue
        reference = entry.get("path")
        if not isinstance(reference, str) or _normalise_local_reference(reference) != reference:
            errors.append(f"manifest.files[{index}].path must be a safe relative path")
            continue
        if reference in seen:
            errors.append(f"manifest.files[{index}].path duplicates {reference!r}")
            continue
        seen.add(reference)
        path = output_dir / reference
        if not path.is_file():
            errors.append(f"missing generated file: {reference}")
            continue
        if path.stat().st_size != entry.get("size_bytes"):
            errors.append(f"size mismatch: {reference}")
        if _sha256(path) != entry.get("sha256"):
            errors.append(f"hash mismatch: {reference}")
    required = {"capture-session.json", "capture.js", "index.html"}
    errors.extend(f"manifest is missing required file: {reference}" for reference in sorted(required - seen))
    data_path = output_dir / "capture-session.json"
    if data_path.is_file():
        try:
            data = load_json(data_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            errors.append(f"capture-session.json could not be read: {error}")
        else:
            if data.get("dashboard_version") != 1:
                errors.append("capture-session.json dashboard_version must be 1")
            if not isinstance(data.get("status"), dict):
                errors.append("capture-session.json status must be an object")
    return errors


def set_capture_session_checkpoints(
    session_path: Path,
    expected_dates: list[str] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Add or replace expected date checkpoints in a capture session."""

    session_path = session_path.resolve()
    session = _load_capture_session(session_path)
    incoming = _normalise_expected_dates(expected_dates)
    existing = _normalise_expected_dates(session.get("expected_dates", []))
    session["expected_dates"] = incoming if replace else sorted(set(existing) | set(incoming))
    write_json(session_path, session)
    coverage = _capture_session_coverage(session_path, session)
    return {
        "session": session_path.name,
        "expected_dates": session["expected_dates"],
        "coverage": coverage,
        "status": coverage["status"],
        "complete": coverage["complete"],
        "next_action": coverage["next_action"],
    }


def finalize_capture_session(
    session_path: Path,
    output_path: Path,
    reached_start: bool = False,
    reached_end: bool = False,
) -> dict[str, Any]:
    """Merge a session's tracked ranges and record the resulting archive path."""

    session_path = session_path.resolve()
    session = _load_capture_session(session_path)
    capture_paths = _session_capture_paths(session_path, session)
    if not capture_paths:
        raise ValueError("capture session has no capture ranges to finalize")
    output_path = output_path.resolve()
    if output_path == session_path:
        raise ValueError("finalized archive cannot overwrite its capture session")
    if output_path.parent != session_path.parent:
        raise ValueError("finalized archive and session file must be in the same directory")
    if output_path in capture_paths:
        raise ValueError("finalized archive cannot overwrite a source capture")

    effective_reached_start = bool(session.get("reached_start")) or reached_start
    effective_reached_end = bool(session.get("reached_end")) or reached_end
    summary = merge_transcripts(
        capture_paths,
        output_path,
        reached_start=effective_reached_start,
        reached_end=effective_reached_end,
        expected_dates=_normalise_expected_dates(session.get("expected_dates", [])),
    )
    session["reached_start"] = effective_reached_start
    session["reached_end"] = effective_reached_end
    session["finalized_archive"] = output_path.relative_to(session_path.parent).as_posix()
    session["finalized_at"] = _capture_session_timestamp()
    session["coverage"] = summary["coverage"]
    write_json(session_path, session)
    return {"output": session["finalized_archive"], **summary}


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


def _evidence_coverage_summary(coverage: Any) -> dict[str, Any]:
    """Keep coverage metrics and boundaries while excluding free-form notes."""

    if not isinstance(coverage, dict):
        return {
            "status": "unverified",
            "complete": False,
            "range_count": 0,
            "unique_message_count": 0,
            "duplicate_message_count": 0,
            "conflict_count": 0,
            "start_confirmed": False,
            "end_confirmed": False,
            "ranges_linked": False,
            "unlinked_range_count": 0,
            "notes_count": 0,
            "ranges": [],
        }

    summary: dict[str, Any] = {}
    for key in (
        "status",
        "complete",
        "range_count",
        "unique_message_count",
        "duplicate_message_count",
        "conflict_count",
        "start_confirmed",
        "end_confirmed",
        "ranges_linked",
    ):
        value = coverage.get(key)
        if key == "status":
            if isinstance(value, str) and value.strip():
                summary[key] = value.strip()
        elif key == "complete" or key.endswith("_confirmed") or key == "ranges_linked":
            summary[key] = bool(value)
        elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            summary[key] = value
    unlinked = coverage.get("unlinked_ranges")
    notes = coverage.get("notes")
    summary["unlinked_range_count"] = len(unlinked) if isinstance(unlinked, list) else 0
    summary["notes_count"] = len(notes) if isinstance(notes, list) else 0
    for key in ("expected_dates", "missing_expected_dates"):
        values = coverage.get(key)
        if isinstance(values, list):
            summary[key] = [
                value.strip()
                for value in values
                if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip())
            ]
    raw_checkpoints = coverage.get("checkpoints")
    checkpoints: list[dict[str, Any]] = []
    if isinstance(raw_checkpoints, list):
        for checkpoint in raw_checkpoints:
            if not isinstance(checkpoint, dict):
                continue
            date = checkpoint.get("date")
            if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date.strip()):
                continue
            range_count = checkpoint.get("range_count")
            checkpoints.append({
                "date": date.strip(),
                "observed": bool(checkpoint.get("observed")),
                "range_count": range_count if isinstance(range_count, int) and not isinstance(range_count, bool) and range_count >= 0 else 0,
            })
    summary["checkpoints"] = checkpoints

    public_ranges = coverage.get("ranges")
    ranges: list[dict[str, Any]] = []
    if isinstance(public_ranges, list):
        for item in public_ranges:
            if not isinstance(item, dict):
                continue
            cleaned: dict[str, Any] = {}
            source_file = _normalise_local_reference(item.get("source_file"))
            if source_file:
                cleaned["source_file"] = source_file
            for key in (
                "message_count",
                "oldest_message_id",
                "oldest_timestamp",
                "newest_message_id",
                "newest_timestamp",
            ):
                value = item.get(key)
                if value is None:
                    cleaned[key] = None
                elif isinstance(value, (str, int)) and not isinstance(value, bool):
                    cleaned[key] = value
            for key in ("at_start", "at_end", "has_capture_range"):
                cleaned[key] = bool(item.get(key))
            overlap = item.get("overlap_with_previous")
            if isinstance(overlap, int) and not isinstance(overlap, bool) and overlap >= 0:
                cleaned["overlap_with_previous"] = overlap
            ranges.append(cleaned)
    summary["ranges"] = ranges
    return summary


def _evidence_source_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    """Select provenance metadata that is useful without copying source notes."""

    source = metadata.get("source")
    if not isinstance(source, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in ("type", "label"):
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            summary[key] = value.strip()
    source_file = _normalise_local_reference(source.get("source_file"))
    if source_file:
        summary["source_file"] = source_file
    capture_files = source.get("capture_files")
    if isinstance(capture_files, list):
        summary["capture_files"] = [
            reference
            for item in capture_files
            for reference in [_normalise_local_reference(item)]
            if reference
        ]
    notes = source.get("notes")
    if isinstance(notes, list):
        summary["notes_count"] = len(notes)

    import_summary = source.get("import_summary")
    if isinstance(import_summary, dict):
        diagnostics: dict[str, Any] = {}
        for key in (
            "files_seen",
            "files_read",
            "records_seen",
            "records_imported",
            "records_skipped",
        ):
            value = import_summary.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                diagnostics[key] = value
        unreadable = import_summary.get("unreadable_files")
        if isinstance(unreadable, list):
            diagnostics["unreadable_files"] = [
                reference
                for item in unreadable
                for reference in [_normalise_local_reference(item)]
                if reference
            ]
        skipped_by_reason = import_summary.get("skipped_by_reason")
        if isinstance(skipped_by_reason, dict):
            diagnostics["skipped_by_reason"] = {
                str(key): value
                for key, value in skipped_by_reason.items()
                if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0
            }
        if diagnostics:
            summary["import_summary"] = diagnostics
    return summary


def _evidence_archive_summary(input_path: Path, archive: dict[str, Any]) -> dict[str, Any]:
    metadata = archive.get("metadata") if isinstance(archive.get("metadata"), dict) else {}
    messages = archive.get("messages") if isinstance(archive.get("messages"), list) else []
    participants = archive.get("participants") if isinstance(archive.get("participants"), list) else []
    ordered_timestamps = sorted(
        parsed
        for message in messages
        if isinstance(message, dict)
        for parsed in [parse_timestamp(message.get("timestamp"))]
        if parsed is not None
    )
    message_ids = [
        str(message.get("id"))
        for message in messages
        if isinstance(message, dict) and message.get("id") is not None
    ]
    attachment_count = 0
    embed_count = 0
    reaction_count = 0
    reply_count = 0
    edited_count = 0
    provenance_count = 0
    source_display_count = 0
    generated_id_count = 0
    call_count = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        for field, target in (
            ("attachments", "attachments"),
            ("embeds", "embeds"),
            ("reactions", "reactions"),
        ):
            value = message.get(field)
            if isinstance(value, list):
                if target == "attachments":
                    attachment_count += len(value)
                elif target == "embeds":
                    embed_count += len(value)
                else:
                    reaction_count += len(value)
        if isinstance(message.get("reply_to"), str) and message["reply_to"].strip():
            reply_count += 1
        if message.get("edited_at") is not None:
            edited_count += 1
        provenance = message.get("provenance")
        if isinstance(provenance, dict):
            provenance_count += 1
            if provenance.get("id_generated") is True:
                generated_id_count += 1
        if isinstance(message.get("source_display"), dict):
            source_display_count += 1
        if isinstance(message.get("call"), dict):
            call_count += 1

    result: dict[str, Any] = {
        "path": input_path.name,
        "size_bytes": input_path.stat().st_size,
        "sha256": _sha256(input_path),
        "schema_version": archive.get("schema_version"),
        "title": str(metadata.get("title") or input_path.stem),
        "kind": str(metadata.get("kind") or "conversation"),
        "message_count": len(messages),
        "participant_count": len(participants),
        "message_id_count": len(message_ids),
        "unique_message_id_count": len(set(message_ids)),
        "oldest_timestamp": ordered_timestamps[0].isoformat().replace("+00:00", "Z") if ordered_timestamps else None,
        "newest_timestamp": ordered_timestamps[-1].isoformat().replace("+00:00", "Z") if ordered_timestamps else None,
        "feature_counts": {
            "attachments": attachment_count,
            "embeds": embed_count,
            "reactions": reaction_count,
            "calls": call_count,
            "replies": reply_count,
            "edited_messages": edited_count,
            "messages_with_provenance": provenance_count,
            "messages_with_source_display": source_display_count,
            "generated_message_ids": generated_id_count,
        },
        "media_diagnostics": _capture_media_summary(archive),
        "local_asset_count": len(_asset_references(archive)),
    }
    for key in ("channel_id", "captured_at", "display_timezone"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def _evidence_asset_records(archive: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    records: list[dict[str, Any]] = []
    for reference in sorted(_asset_references(archive)):
        path = (root / Path(reference)).resolve()
        record: dict[str, Any] = {
            "path": reference,
            "exists": False,
            "size_bytes": None,
            "sha256": None,
        }
        try:
            path.relative_to(root)
        except ValueError:
            record["status"] = "outside_archive_directory"
            records.append(record)
            continue
        if path.is_file():
            record["exists"] = True
            record["size_bytes"] = path.stat().st_size
            record["sha256"] = _sha256(path)
        records.append(record)
    return records


def _copy_evidence_inputs(
    paths: list[Path] | None,
    root: Path,
    kind: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source_value in paths or []:
        source = source_value.resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Evidence {kind} file does not exist: {source}")
        size_bytes = source.stat().st_size
        if size_bytes > _MAX_EVIDENCE_ATTACHMENT_BYTES:
            raise ValueError(
                f"Evidence {kind} file exceeds the {_MAX_EVIDENCE_ATTACHMENT_BYTES // (1024 * 1024)} MB limit: {source.name}"
            )
        digest = _sha256(source)
        filename = _safe_media_filename(source.name, f"evidence-{kind}")
        destination = root / "evidence" / kind / f"{digest[:12]}-{filename}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source != destination:
            shutil.copy2(source, destination)
        records.append({
            "kind": kind,
            "path": destination.relative_to(root).as_posix(),
            "size_bytes": size_bytes,
            "sha256": digest,
            "mime": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
        })
    return records


def _evidence_capture_session_summary(
    session_path: Path,
    status: dict[str, Any],
    archive_name: str,
) -> dict[str, Any]:
    captures: list[dict[str, Any]] = []
    for capture in status.get("captures", []) if isinstance(status.get("captures"), list) else []:
        if not isinstance(capture, dict):
            continue
        cleaned: dict[str, Any] = {}
        path = _normalise_local_reference(capture.get("path"))
        if path:
            cleaned["path"] = path
        for key in (
            "sha256",
            "message_count",
            "oldest_message_id",
            "oldest_timestamp",
            "newest_message_id",
            "newest_timestamp",
        ):
            value = capture.get(key)
            if value is not None:
                cleaned[key] = value
        for key in ("at_start", "at_end", "has_capture_range"):
            cleaned[key] = bool(capture.get(key))
        evidence = capture.get("evidence", [])
        if isinstance(evidence, list):
            cleaned["evidence"] = [
                {
                    key: record[key]
                    for key in ("version", "kind", "path", "size_bytes", "sha256", "mime")
                    if key in record
                }
                for record in evidence
                if isinstance(record, dict)
            ]
        captures.append(cleaned)
    raw_finalized_archive = status.get("finalized_archive")
    finalized_archive = _normalise_local_reference(raw_finalized_archive)
    if raw_finalized_archive is not None and finalized_archive is None:
        raise ValueError("Capture session finalized_archive must be a safe relative path")
    return {
        "path": session_path.name,
        "size_bytes": session_path.stat().st_size,
        "sha256": _sha256(session_path),
        "channel_id": status.get("channel_id") if isinstance(status.get("channel_id"), str) else None,
        "capture_count": int(status.get("capture_count") or 0),
        "status": str(status.get("status") or "unverified"),
        "complete": bool(status.get("complete")),
        "reached_start": bool(status.get("reached_start")),
        "reached_end": bool(status.get("reached_end")),
        "finalized_archive": finalized_archive,
        "archive_match": None if finalized_archive is None else finalized_archive == archive_name,
        "coverage": _evidence_coverage_summary(status.get("coverage")),
        "media": status.get("media") if isinstance(status.get("media"), dict) else {},
        "evidence": status.get("evidence") if isinstance(status.get("evidence"), dict) else {},
        "captures": captures,
    }


def _capture_session_evidence_paths(
    session_path: Path,
    status: dict[str, Any],
) -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = {"dom": [], "screenshots": []}
    captures = status.get("captures", []) if isinstance(status.get("captures"), list) else []
    for capture in captures:
        if not isinstance(capture, dict):
            continue
        evidence = capture.get("evidence", [])
        if not isinstance(evidence, list):
            continue
        for record in evidence:
            if not isinstance(record, dict):
                continue
            kind = record.get("kind")
            relative = _normalise_local_reference(record.get("path"))
            if kind not in paths or relative is None:
                continue
            paths[kind].append((session_path.parent / Path(relative)).resolve())
    return paths


def export_evidence(
    input_path: Path,
    output_path: Path,
    session_path: Path | None = None,
    dom_paths: list[Path] | None = None,
    screenshot_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Export a private provenance report beside an archive.

    Optional DOM snapshots and screenshots are copied into the evidence bundle
    only when explicitly supplied by the user.
    """

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Archive file does not exist: {input_path}")
    if output_path == input_path:
        raise ValueError("Evidence report cannot overwrite the archive")
    if output_path.parent != input_path.parent:
        raise ValueError("Evidence report and archive must be in the same directory")

    archive = load_json(input_path)
    errors = validate_archive(archive)
    if errors:
        raise ValueError("Archive validation failed:\n- " + "\n- ".join(errors))

    session_status: dict[str, Any] | None = None
    attached_evidence: dict[str, list[Path]] = {"dom": [], "screenshots": []}
    if session_path is not None:
        session_path = session_path.resolve()
        if session_path == output_path:
            raise ValueError("Evidence report cannot overwrite its capture session")
        if session_path.parent != input_path.parent:
            raise ValueError("Evidence report, archive, and capture session must be in the same directory")
        session_status = capture_session_status(session_path)
        attached_evidence = _capture_session_evidence_paths(session_path, session_status)

    rendered_evidence: list[dict[str, Any]] = []
    seen_rendered: set[tuple[str, str]] = set()
    for kind, paths in (
        ("dom", list(dom_paths or []) + attached_evidence["dom"]),
        ("screenshots", list(screenshot_paths or []) + attached_evidence["screenshots"]),
    ):
        for record in _copy_evidence_inputs(paths, input_path.parent, kind):
            key = (str(record.get("kind")), str(record.get("sha256")))
            if key in seen_rendered:
                continue
            seen_rendered.add(key)
            rendered_evidence.append(record)

    evidence: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "type": EVIDENCE_TYPE,
        "generated_at": _capture_session_timestamp(),
        "archive": _evidence_archive_summary(input_path, archive),
        "source": _evidence_source_summary(archive.get("metadata") or {}),
        "coverage": _evidence_coverage_summary(verify_transcript_coverage(input_path)),
        "local_assets": _evidence_asset_records(archive, input_path.parent),
        "rendered_evidence": rendered_evidence,
        "capture_session": None,
    }

    if session_path is not None:
        evidence["capture_session"] = _evidence_capture_session_summary(
            session_path,
            session_status or {},
            input_path.name,
        )

    write_json(output_path, evidence)
    return evidence


def _evidence_resolve_reference(root: Path, value: Any, field: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or _normalise_local_reference(value) != value:
        errors.append(f"{field} must be a safe relative path")
        return None
    path = (root / Path(value)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        errors.append(f"{field} escapes the evidence directory")
        return None
    return path


def _verify_evidence_file_record(
    record: Any,
    path: Path | None,
    field: str,
    errors: list[str],
) -> None:
    if not isinstance(record, dict):
        errors.append(f"{field} must be an object")
        return
    if path is None:
        return
    if not path.is_file():
        errors.append(f"{field}.path is missing: {record.get('path')}")
        return
    size_bytes = record.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        errors.append(f"{field}.size_bytes must be a non-negative integer")
    elif path.stat().st_size != size_bytes:
        errors.append(f"{field} size mismatch: {record.get('path')}")
    expected_hash = record.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        errors.append(f"{field}.sha256 must be a SHA-256 hex digest")
    elif _sha256(path).lower() != expected_hash.lower():
        errors.append(f"{field} hash mismatch: {record.get('path')}")


def verify_evidence(evidence_path: Path) -> list[str]:
    """Verify an evidence report, its archive, local assets, and session link."""

    evidence_path = evidence_path.resolve()
    errors: list[str] = []
    if not evidence_path.is_file():
        return [f"evidence report does not exist: {evidence_path}"]
    try:
        evidence = load_json(evidence_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        return [f"evidence report could not be read: {error}"]
    if evidence.get("evidence_version") != EVIDENCE_VERSION:
        errors.append(f"evidence_version must be {EVIDENCE_VERSION}")
    if evidence.get("type") != EVIDENCE_TYPE:
        errors.append(f"evidence type must be {EVIDENCE_TYPE}")

    root = evidence_path.parent
    archive_record = evidence.get("archive")
    archive_path: Path | None = None
    if not isinstance(archive_record, dict):
        errors.append("archive must be an object")
    else:
        archive_path = _evidence_resolve_reference(root, archive_record.get("path"), "archive.path", errors)
        if archive_path is not None and archive_path.parent != root:
            errors.append("archive.path must point beside the evidence report")
        _verify_evidence_file_record(archive_record, archive_path, "archive", errors)

    archive: dict[str, Any] | None = None
    if archive_path is not None and archive_path.is_file():
        try:
            archive_value = load_json(archive_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"archive could not be read: {error}")
        else:
            if not isinstance(archive_value, dict):
                errors.append("archive must be a JSON object")
            else:
                archive = archive_value
                errors.extend(f"archive: {error}" for error in validate_archive(archive))
                if isinstance(archive_record, dict):
                    current_summary = _evidence_archive_summary(archive_path, archive)
                    for key in ("message_count", "participant_count", "sha256"):
                        if archive_record.get(key) != current_summary.get(key):
                            errors.append(f"archive summary mismatch: {key}")

    asset_records = evidence.get("local_assets")
    reported_assets: set[str] = set()
    if not isinstance(asset_records, list):
        errors.append("local_assets must be an array")
        asset_records = []
    for index, record in enumerate(asset_records):
        if not isinstance(record, dict):
            errors.append(f"local_assets[{index}] must be an object")
            continue
        reference = record.get("path")
        path = _evidence_resolve_reference(root, reference, f"local_assets[{index}].path", errors)
        if isinstance(reference, str) and _normalise_local_reference(reference) == reference:
            if reference in reported_assets:
                errors.append(f"local_assets[{index}].path duplicates {reference!r}")
            reported_assets.add(reference)
        exists = record.get("exists")
        if not isinstance(exists, bool):
            errors.append(f"local_assets[{index}].exists must be boolean")
            exists = False
        if exists:
            _verify_evidence_file_record(record, path, f"local_assets[{index}]", errors)
        elif path is not None and path.is_file():
            errors.append(f"local_assets[{index}] is marked missing but exists: {reference}")
        elif path is not None:
            errors.append(f"missing local asset: {reference}")

    if archive is not None:
        expected_assets = _asset_references(archive)
        errors.extend(
            f"evidence is missing asset record: {reference}"
            for reference in sorted(expected_assets - reported_assets)
        )
        errors.extend(
            f"evidence lists an unexpected asset: {reference}"
            for reference in sorted(reported_assets - expected_assets)
        )

    # Evidence reports created before rendered-DOM/screenshot support remain
    # verifiable. A present field must still have the current strict shape.
    rendered_records = evidence.get("rendered_evidence", [])
    if not isinstance(rendered_records, list):
        errors.append("rendered_evidence must be an array")
        rendered_records = []
    rendered_paths: set[str] = set()
    for index, record in enumerate(rendered_records):
        if not isinstance(record, dict):
            errors.append(f"rendered_evidence[{index}] must be an object")
            continue
        kind = record.get("kind")
        if kind not in {"dom", "screenshots"}:
            errors.append(f"rendered_evidence[{index}].kind must be dom or screenshots")
        reference = record.get("path")
        path = _evidence_resolve_reference(root, reference, f"rendered_evidence[{index}].path", errors)
        if isinstance(reference, str) and _normalise_local_reference(reference) == reference:
            if reference in rendered_paths:
                errors.append(f"rendered_evidence[{index}].path duplicates {reference!r}")
            rendered_paths.add(reference)
        _verify_evidence_file_record(record, path, f"rendered_evidence[{index}]", errors)

    session_record = evidence.get("capture_session")
    if session_record is not None:
        if not isinstance(session_record, dict):
            errors.append("capture_session must be an object or null")
        else:
            session_path = _evidence_resolve_reference(root, session_record.get("path"), "capture_session.path", errors)
            if session_path is not None and session_path.parent != root:
                errors.append("capture_session.path must point beside the evidence report")
            _verify_evidence_file_record(session_record, session_path, "capture_session", errors)
            if session_path is not None and session_path.is_file():
                try:
                    status = capture_session_status(session_path)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                    errors.append(f"capture session could not be verified: {error}")
                else:
                    if session_record.get("status") != status.get("status"):
                        errors.append("capture session summary mismatch: status")
                    if session_record.get("capture_count") != status.get("capture_count"):
                        errors.append("capture session summary mismatch: capture_count")
                    finalized_archive = _normalise_local_reference(status.get("finalized_archive"))
                    archive_reference = archive_record.get("path") if isinstance(archive_record, dict) else None
                    expected_match = None if finalized_archive is None else finalized_archive == archive_reference
                    if session_record.get("archive_match") != expected_match:
                        errors.append("capture session summary mismatch: archive_match")

    return errors


def _stable_identifier(value: str, fallback_index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return f"author-{slug or fallback_index + 1}"


def _normalise_profile(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, Any] = {}
    for key in ("presence", "pronouns", "member_since", "source", "custom_status"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            normalized[key] = item.strip()
    for key in ("banner_path", "avatar_decoration_path"):
        path = _normalise_local_reference(value.get(key))
        if path:
            normalized[key] = path
    for key in ("banner_ref", "avatar_decoration_ref"):
        reference = value.get(key)
        if _is_http_url(reference):
            normalized[key] = reference.strip()
    activities: list[str] = []
    raw_activities = value.get("activities")
    if isinstance(raw_activities, list):
        for item in raw_activities:
            if isinstance(item, str) and item.strip() and item.strip() not in activities:
                activities.append(item.strip())
    if activities:
        normalized["activities"] = activities
    captured_at = value.get("captured_at")
    if normalise_timestamp(captured_at) and _has_timestamp_timezone(captured_at):
        normalized["captured_at"] = normalise_timestamp(captured_at)

    badges: list[dict[str, Any]] = []
    raw_badges = value.get("badges")
    if isinstance(raw_badges, list):
        for item in raw_badges:
            if not isinstance(item, dict):
                continue
            badge: dict[str, Any] = {}
            for key in ("label", "detail"):
                field = item.get(key)
                if isinstance(field, str) and field.strip():
                    badge[key] = field.strip()
            icon_path = _normalise_local_reference(item.get("icon_path"))
            icon_ref = item.get("icon_ref")
            if icon_path:
                badge["icon_path"] = icon_path
            if _is_http_url(icon_ref):
                badge["icon_ref"] = icon_ref.strip()
            if badge:
                badges.append(badge)
    if badges:
        normalized["badges"] = badges

    friends: list[dict[str, Any]] = []
    raw_friends = value.get("mutual_friends")
    if isinstance(raw_friends, list):
        for item in raw_friends:
            if not isinstance(item, dict):
                continue
            friend: dict[str, Any] = {}
            for key in ("id", "display_name", "username", "avatar_alt"):
                field = item.get(key)
                if isinstance(field, str) and field.strip():
                    friend[key] = field.strip()
            avatar_path = _normalise_local_reference(item.get("avatar_path"))
            avatar_ref = item.get("avatar_ref")
            if avatar_path:
                friend["avatar_path"] = avatar_path
            if _is_http_url(avatar_ref):
                friend["avatar_ref"] = avatar_ref.strip()
            if friend:
                friends.append(friend)
    if friends:
        normalized["mutual_friends"] = friends
    return normalized or None


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
    profile = _normalise_profile(_first(record, "profile", "Profile", default=None))
    if profile:
        participant["profile"] = profile
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
    raw_stickers = _first(record, "stickers", "Stickers", "sticker_items", "Sticker Items", "stickerItems", default=[])
    raw_custom_emojis = _first(
        record,
        "custom_emojis",
        "customEmojis",
        "Custom Emojis",
        "emoji_assets",
        "emojiAssets",
        default=[],
    )
    if raw_reactions is None:
        raw_reactions = []
    if raw_embeds is None:
        raw_embeds = []
    if raw_stickers is None:
        raw_stickers = []
    if raw_custom_emojis is None:
        raw_custom_emojis = []
    raw_call = _first(record, "call", "Call", "call_event", "callEvent", default=None)
    message: dict[str, Any] = {
        "id": message_id,
        "author_id": resolved_author_id,
        "timestamp": timestamp,
        "content": content,
        "attachments": [_normalise_attachment(item) for item in raw_attachments],
        "reactions": [_normalise_reaction(item) for item in (raw_reactions if isinstance(raw_reactions, list) else [raw_reactions])],
        "embeds": [_normalise_embed(item) for item in (raw_embeds if isinstance(raw_embeds, list) else [raw_embeds])],
        "stickers": [_normalise_media_reference(item, "sticker") for item in (raw_stickers if isinstance(raw_stickers, list) else [raw_stickers])],
        "custom_emojis": [_normalise_media_reference(item, "custom emoji") for item in (raw_custom_emojis if isinstance(raw_custom_emojis, list) else [raw_custom_emojis])],
        "provenance": {
            "source_file": source_file,
            "record_index": index,
        },
    }
    normalized_call = _normalise_call(raw_call)
    if normalized_call is not None:
        message["call"] = normalized_call
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
    if isinstance(input_metadata.get("capture_diagnostics"), dict):
        metadata["capture_diagnostics"] = input_metadata["capture_diagnostics"]
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


def _redacted_count(value: Any, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _redact_coverage(value: Any, message_id_map: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    redacted: dict[str, Any] = {}
    for key in (
        "version",
        "status",
        "complete",
        "range_count",
        "unique_message_count",
        "duplicate_message_count",
        "conflict_count",
        "start_confirmed",
        "end_confirmed",
        "ranges_linked",
    ):
        if key in value and isinstance(value[key], (str, int, bool)) and not isinstance(value[key], float):
            redacted[key] = value[key]
    for key in ("expected_dates", "missing_expected_dates"):
        items = value.get(key)
        if isinstance(items, list):
            redacted[key] = [item for item in items if isinstance(item, str)]
    checkpoints = value.get("checkpoints")
    if isinstance(checkpoints, list):
        redacted["checkpoints"] = [
            {
                "date": item.get("date"),
                "observed": bool(item.get("observed")),
                "range_count": _redacted_count(item.get("range_count")),
            }
            for item in checkpoints
            if isinstance(item, dict) and isinstance(item.get("date"), str)
        ]
    ranges = value.get("ranges")
    if isinstance(ranges, list):
        redacted_ranges: list[dict[str, Any]] = []
        for index, item in enumerate(ranges, start=1):
            if not isinstance(item, dict):
                continue
            cleaned: dict[str, Any] = {
                "source_file": f"range-{index:03d}.json",
                "message_count": _redacted_count(item.get("message_count")),
                "oldest_message_id": message_id_map.get(str(item.get("oldest_message_id")), None) if item.get("oldest_message_id") is not None else None,
                "oldest_timestamp": item.get("oldest_timestamp"),
                "newest_message_id": message_id_map.get(str(item.get("newest_message_id")), None) if item.get("newest_message_id") is not None else None,
                "newest_timestamp": item.get("newest_timestamp"),
                "at_start": bool(item.get("at_start")),
                "at_end": bool(item.get("at_end")),
                "has_capture_range": bool(item.get("has_capture_range", True)),
                "overlap_with_previous": _redacted_count(item.get("overlap_with_previous")),
            }
            redacted_ranges.append(cleaned)
        redacted["ranges"] = redacted_ranges
    redacted["unlinked_ranges"] = []
    redacted["notes"] = ["Coverage metrics were retained; source filenames and message IDs were redacted."]
    redacted["next_action"] = (
        "No further capture step is required for the observed rendered range."
        if redacted.get("complete")
        else "The redacted archive retains the source coverage status; capture more ranges from the original conversation if needed."
    )
    return redacted


def redact_archive(input_path: Path, output_path: Path, profile: str = "safe-share") -> dict[str, Any]:
    """Create a deterministic safe-share copy without source identities or media."""

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Archive file does not exist: {input_path}")
    if input_path == output_path:
        raise ValueError("Redaction cannot overwrite the source archive")
    if profile != "safe-share":
        raise ValueError("Only the safe-share redaction profile is supported")
    archive = load_json(input_path)
    errors = validate_archive(archive)
    if errors:
        raise ValueError("Cannot redact an invalid archive:\n- " + "\n- ".join(errors))

    participants = archive.get("participants") if isinstance(archive.get("participants"), list) else []
    participant_map: dict[str, str] = {}
    redacted_participants: list[dict[str, Any]] = []
    for index, participant in enumerate(participants, start=1):
        if not isinstance(participant, dict):
            continue
        old_id = str(participant.get("id") or f"participant-{index}")
        new_id = f"participant-{index:03d}"
        participant_map[old_id] = new_id
        redacted_participants.append({
            "id": new_id,
            "username": f"participant-{index:03d}",
            "display_name": f"Participant {index}",
            "avatar_path": None,
        })

    messages = archive.get("messages") if isinstance(archive.get("messages"), list) else []
    message_id_map: dict[str, str] = {}
    for index, message in enumerate(messages, start=1):
        if isinstance(message, dict) and message.get("id") is not None:
            message_id_map[str(message["id"])] = f"message-{index:06d}"

    redacted_messages: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        new_message: dict[str, Any] = {
            "id": message_id_map.get(str(message.get("id")), f"message-{index:06d}"),
            "author_id": participant_map.get(str(message.get("author_id")), "participant-001"),
            "timestamp": message.get("timestamp"),
            "content": "[redacted]" if str(message.get("content") or "") else "",
            "attachments": [],
            "reactions": [
                {"emoji": "reaction", "count": _redacted_count(reaction.get("count"), 1)}
                for reaction in message.get("reactions", [])
                if isinstance(reaction, dict)
            ],
            "embeds": [],
            "provenance": {"redacted": True},
        }
        if message.get("reply_to") is not None:
            new_message["reply_to"] = message_id_map.get(str(message.get("reply_to")))
        if new_message.get("reply_to") is None:
            new_message.pop("reply_to", None)
        if message.get("edited_at") is not None:
            new_message["edited_at"] = message.get("edited_at")
        if message.get("grouped") is not None:
            new_message["grouped"] = bool(message.get("grouped"))
        call = message.get("call")
        if isinstance(call, dict):
            new_message["call"] = {
                "type": "voice",
                "status": call.get("status") if call.get("status") in {"completed", "missed"} else "completed",
                "duration_label": str(call.get("duration_label") or "duration unavailable"),
            }
        source_display = message.get("source_display")
        if isinstance(source_display, dict):
            safe_display = {
                key: source_display[key]
                for key in ("date", "time")
                if isinstance(source_display.get(key), str) and source_display[key].strip()
            }
            if safe_display:
                new_message["source_display"] = safe_display
        redacted_messages.append(new_message)

    metadata = archive.get("metadata") if isinstance(archive.get("metadata"), dict) else {}
    redacted_metadata: dict[str, Any] = {
        "kind": metadata.get("kind").strip() if isinstance(metadata.get("kind"), str) and metadata.get("kind").strip() else "conversation",
        "title": "Redacted conversation",
        "display_timezone": metadata.get("display_timezone") if isinstance(metadata.get("display_timezone"), str) else "UTC",
        "source": {
            "type": "redacted_archive",
            "label": "Concordance safe-share redaction",
            "notes": [
                "Generated by the safe-share redaction profile.",
                "Message content, source identifiers, links, avatars, attachments, embeds, and media references were removed or replaced.",
                "Timestamps and selected coverage metrics were retained for layout and audit testing.",
            ],
        },
        "redaction": {
            "profile": "safe-share",
            "content": "replaced",
            "identifiers": "remapped",
            "media": "removed",
            "participants": "anonymized",
        },
    }
    for key in ("coverage", "capture_range"):
        sanitized = _redact_coverage(metadata.get(key), message_id_map) if key == "coverage" else None
        if key == "capture_range" and isinstance(metadata.get(key), dict):
            capture_range = metadata[key]
            sanitized = {
                "version": capture_range.get("version", 1),
                "message_count": _redacted_count(capture_range.get("message_count"), len(redacted_messages)),
                "oldest_message_id": message_id_map.get(str(capture_range.get("oldest_message_id")), None),
                "oldest_timestamp": capture_range.get("oldest_timestamp"),
                "newest_message_id": message_id_map.get(str(capture_range.get("newest_message_id")), None),
                "newest_timestamp": capture_range.get("newest_timestamp"),
                "at_start": bool(capture_range.get("at_start")),
                "at_end": bool(capture_range.get("at_end")),
            }
        if sanitized is not None:
            redacted_metadata[key] = sanitized

    redacted_archive = {
        "schema_version": SCHEMA_VERSION,
        "metadata": redacted_metadata,
        "participants": redacted_participants,
        "messages": redacted_messages,
    }
    errors = validate_archive(redacted_archive)
    if errors:
        raise ValueError("Redacted archive did not validate:\n- " + "\n- ".join(errors))
    write_json(output_path, redacted_archive)
    return redacted_archive


def migrate_archive(input_path: Path, output_path: Path, target_version: int = SCHEMA_VERSION) -> dict[str, Any]:
    """Migrate a legacy archive into the current normalized schema version."""

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path == output_path:
        raise ValueError("Migration cannot overwrite the source archive")
    if target_version != SCHEMA_VERSION:
        raise ValueError(f"Only target schema version {SCHEMA_VERSION} is supported")
    if not input_path.is_file():
        raise FileNotFoundError(f"Archive file does not exist: {input_path}")
    with input_path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Archive migration input must be a JSON object")
    current_version = value.get("schema_version", 0)
    if isinstance(current_version, bool) or not isinstance(current_version, int):
        raise ValueError("Archive schema_version must be an integer")
    migration_path = _SCHEMA_MIGRATION_PATHS.get(current_version)
    if migration_path is None:
        raise ValueError(f"No migration path exists from schema version {current_version!r} to {SCHEMA_VERSION}")
    if current_version == SCHEMA_VERSION:
        errors = validate_archive(value)
        if errors:
            raise ValueError("Current archive is invalid:\n- " + "\n- ".join(errors))
        write_json(output_path, value)
        return {
            "from_version": SCHEMA_VERSION,
            "to_version": SCHEMA_VERSION,
            "migration_path": list(migration_path),
            "changed": False,
            "archive": value,
        }
    migrated = import_transcript(input_path, output_path)
    return {
        "from_version": current_version,
        "to_version": SCHEMA_VERSION,
        "migration_path": list(migration_path),
        "changed": True,
        "archive": migrated,
    }


_BUNDLE_MAX_BYTES = 512 * 1024 * 1024
_ENCRYPTED_MAGIC = b"CONCORDANCE-ENCRYPTED\x01"
_ENCRYPTED_AAD = b"concordance-encrypted-bundle-v1"
_PBKDF2_ITERATIONS = 600_000
_MAX_PBKDF2_ITERATIONS = 5_000_000


def _bundle_file_entries(input_path: Path) -> list[tuple[str, Path]]:
    input_path = input_path.resolve()
    if input_path.is_file():
        return [(input_path.name, input_path)]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Bundle input does not exist: {input_path}")
    entries: list[tuple[str, Path]] = []
    for path in sorted(input_path.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(input_path).as_posix()
        except ValueError as error:
            raise ValueError(f"Bundle input contains a file outside its directory: {path.name}") from error
        if _normalise_local_reference(relative) != relative:
            raise ValueError(f"Bundle input contains an unsafe relative path: {relative}")
        entries.append((relative, path))
    return entries


def _bundle_bytes(input_path: Path) -> bytes:
    entries = _bundle_file_entries(input_path)
    total = sum(path.stat().st_size for _, path in entries)
    if total > _BUNDLE_MAX_BYTES:
        raise ValueError(f"Bundle input exceeds the {_BUNDLE_MAX_BYTES // (1024 * 1024)} MB limit")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for relative, path in entries:
            bundle.write(path, arcname=relative)
    return buffer.getvalue()


def _safe_bundle_member(value: str) -> str | None:
    candidate = value.replace("\\", "/")
    if not candidate or candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
        return None
    parts = [part for part in candidate.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _extract_bundle_bytes(data: bytes, output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise FileExistsError(f"Bundle output directory already exists and is not empty: {output_dir}")
        raise FileExistsError(f"Bundle output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(tempfile.mkdtemp(prefix=".concordance-bundle-", dir=output_dir.parent))
    files = 0
    expanded_bytes = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as bundle:
            for info in bundle.infolist():
                relative = _safe_bundle_member(info.filename)
                if relative is None:
                    raise ValueError(f"Bundle contains an unsafe path: {info.filename!r}")
                if relative in seen:
                    raise ValueError(f"Bundle contains a duplicate path: {relative}")
                seen.add(relative)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError(f"Bundle contains a symbolic link: {relative}")
                if info.is_dir():
                    (temporary_dir / relative).mkdir(parents=True, exist_ok=True)
                    continue
                expanded_bytes += info.file_size
                if expanded_bytes > _BUNDLE_MAX_BYTES:
                    raise ValueError(f"Bundle expands beyond the {_BUNDLE_MAX_BYTES // (1024 * 1024)} MB limit")
                destination = (temporary_dir / relative).resolve()
                try:
                    destination.relative_to(temporary_dir)
                except ValueError as error:
                    raise ValueError(f"Bundle path escapes its output directory: {relative}") from error
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info, "r") as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 256)
                files += 1
        temporary_dir.replace(output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    verified = False
    if (output_dir / "archive.json").is_file() and (output_dir / "index.html").is_file():
        errors = verify_build(output_dir)
        if errors:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise ValueError("Extracted bundle failed viewer verification:\n- " + "\n- ".join(errors))
        verified = True
    return {"output": output_dir, "files": files, "bytes": expanded_bytes, "verified": verified}


def export_bundle(input_path: Path, output_path: Path) -> dict[str, Any]:
    """Export an archive file or generated viewer directory as a portable ZIP."""

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"Bundle output already exists: {output_path}")
    if input_path.is_dir():
        try:
            output_path.relative_to(input_path)
        except ValueError:
            pass
        else:
            raise ValueError("Bundle output cannot be inside its input directory")
    data = _bundle_bytes(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    return {"output": output_path, "bytes": len(data), "files": len(_bundle_file_entries(input_path))}


def import_bundle(input_path: Path, output_dir: Path) -> dict[str, Any]:
    """Safely extract a portable ZIP bundle and verify generated viewers."""

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Bundle file does not exist: {input_path}")
    if input_path.stat().st_size > _BUNDLE_MAX_BYTES:
        raise ValueError(f"Bundle file exceeds the {_BUNDLE_MAX_BYTES // (1024 * 1024)} MB limit")
    return _extract_bundle_bytes(input_path.read_bytes(), output_dir)


def _crypto_aesgcm() -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as error:
        raise RuntimeError("Encrypted bundles require the optional dependency: pip install .[secure]") from error
    return AESGCM, hashes, PBKDF2HMAC


def _derive_bundle_key(password: str, salt: bytes, iterations: int) -> bytes:
    if not isinstance(password, str) or not password:
        raise ValueError("A non-empty bundle password is required")
    _, hashes, PBKDF2HMAC = _crypto_aesgcm()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def encrypt_bundle(input_path: Path, output_path: Path, password: str) -> dict[str, Any]:
    """Encrypt a ZIP bundle with PBKDF2-HMAC-SHA256 and AES-256-GCM."""

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"Encrypted bundle output already exists: {output_path}")
    AESGCM, _, _ = _crypto_aesgcm()
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_bundle_key(password, salt, _PBKDF2_ITERATIONS)
    ciphertext = AESGCM(key).encrypt(nonce, _bundle_bytes(input_path), _ENCRYPTED_AAD)
    header = json.dumps(
        {
            "format": "concordance-encrypted-bundle",
            "version": 1,
            "cipher": "AES-256-GCM",
            "kdf": "PBKDF2-HMAC-SHA256",
            "iterations": _PBKDF2_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        handle.write(_ENCRYPTED_MAGIC)
        handle.write(struct.pack(">I", len(header)))
        handle.write(header)
        handle.write(ciphertext)
    return {"output": output_path, "bytes": output_path.stat().st_size}


def decrypt_bundle(input_path: Path, output_dir: Path, password: str) -> dict[str, Any]:
    """Decrypt and safely extract an AES-GCM encrypted bundle."""

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Encrypted bundle file does not exist: {input_path}")
    if input_path.stat().st_size > _BUNDLE_MAX_BYTES + 64 * 1024 * 1024:
        raise ValueError("Encrypted bundle exceeds the supported size limit")
    AESGCM, _, _ = _crypto_aesgcm()
    data = input_path.read_bytes()
    if not data.startswith(_ENCRYPTED_MAGIC) or len(data) < len(_ENCRYPTED_MAGIC) + 4:
        raise ValueError("Not a Concordance encrypted bundle")
    header_start = len(_ENCRYPTED_MAGIC)
    header_length = struct.unpack(">I", data[header_start : header_start + 4])[0]
    if header_length > 64 * 1024:
        raise ValueError("Encrypted bundle header is too large")
    header_end = header_start + 4 + header_length
    try:
        header = json.loads(data[header_start + 4 : header_end].decode("utf-8"))
        salt = base64.b64decode(header["salt"], validate=True)
        nonce = base64.b64decode(header["nonce"], validate=True)
        iterations = int(header["iterations"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Encrypted bundle header is invalid") from error
    if header.get("format") != "concordance-encrypted-bundle" or header.get("version") != 1 or header.get("cipher") != "AES-256-GCM":
        raise ValueError("Unsupported encrypted bundle format")
    if len(salt) != 16 or len(nonce) != 12 or not 100_000 <= iterations <= _MAX_PBKDF2_ITERATIONS:
        raise ValueError("Encrypted bundle parameters are invalid")
    key = _derive_bundle_key(password, salt, iterations)
    try:
        plaintext = AESGCM(key).decrypt(nonce, data[header_end:], _ENCRYPTED_AAD)
    except Exception as error:
        raise ValueError("Unable to decrypt bundle; the password may be wrong or the file may be corrupt") from error
    return _extract_bundle_bytes(plaintext, output_dir)


def _iter_media_references(archive: dict[str, Any]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []

    def add(scope: str, field: str, local_path: Any = None, remote_url: Any = None, **context: Any) -> None:
        if not _normalise_local_reference(local_path) and not _is_http_url(remote_url):
            return
        record: dict[str, Any] = {
            "scope": scope,
            "field": field,
            "path": _normalise_local_reference(local_path),
            "url": str(remote_url).strip() if _is_http_url(remote_url) else None,
        }
        record.update({key: value for key, value in context.items() if value not in (None, "")})
        references.append(record)

    for participant in archive.get("participants", []) if isinstance(archive.get("participants"), list) else []:
        if not isinstance(participant, dict):
            continue
        participant_id = str(participant.get("id") or participant.get("username") or "participant")
        add("participant", "avatar", participant.get("avatar_path"), participant.get("avatar_ref"), participant_id=participant_id)
        profile = participant.get("profile")
        if not isinstance(profile, dict):
            continue
        for field in ("banner", "avatar_decoration"):
            add(
                "profile",
                field,
                profile.get(f"{field}_path"),
                profile.get(f"{field}_ref"),
                participant_id=participant_id,
            )
        badges = profile.get("badges", [])
        if isinstance(badges, list):
            for index, badge in enumerate(badges):
                if isinstance(badge, dict):
                    add(
                        "profile_badge",
                        "icon",
                        badge.get("icon_path"),
                        badge.get("icon_ref"),
                        participant_id=participant_id,
                        item_index=index,
                    )
        friends = profile.get("mutual_friends", [])
        if isinstance(friends, list):
            for index, friend in enumerate(friends):
                if isinstance(friend, dict):
                    add(
                        "mutual_friend",
                        "avatar",
                        friend.get("avatar_path"),
                        friend.get("avatar_ref"),
                        participant_id=participant_id,
                        item_index=index,
                    )

    for message in archive.get("messages", []) if isinstance(archive.get("messages"), list) else []:
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("id") or "message")
        attachments = message.get("attachments", [])
        if isinstance(attachments, list):
            for index, attachment in enumerate(attachments):
                if isinstance(attachment, dict):
                    add(
                        "attachment",
                        "file",
                        attachment.get("path") or attachment.get("local_path"),
                        attachment.get("url"),
                        message_id=message_id,
                        item_index=index,
                        name=attachment.get("name"),
                    )
        for collection_name in ("stickers", "custom_emojis"):
            collection = message.get(collection_name, [])
            if isinstance(collection, list):
                for index, media in enumerate(collection):
                    if isinstance(media, dict):
                        add(
                            collection_name[:-1],
                            "media",
                            media.get("path") or media.get("preview_path"),
                            media.get("url"),
                            message_id=message_id,
                            item_index=index,
                            name=media.get("name"),
                        )
        embeds = message.get("embeds", [])
        if isinstance(embeds, list):
            for embed_index, embed in enumerate(embeds):
                if not isinstance(embed, dict):
                    continue
                for media_name in ("image", "thumbnail", "video", "audio"):
                    add(
                        "embed",
                        media_name,
                        embed.get(f"{media_name}_path") or embed.get("local_path"),
                        embed.get(f"{media_name}_url"),
                        message_id=message_id,
                        item_index=embed_index,
                        embed_url=embed.get("url"),
                    )
    return references


def audit_archive_media(input_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    """Report offline media readiness without downloading or changing an archive."""

    input_path = input_path.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Archive file does not exist: {input_path}")
    archive = load_json(input_path)
    errors = validate_archive(archive)
    if errors:
        raise ValueError("Cannot audit media from an invalid archive:\n- " + "\n- ".join(errors))

    records: list[dict[str, Any]] = []
    host_counts: dict[str, int] = {}
    counts = {
        "total": 0,
        "offline_ready": 0,
        "downloadable": 0,
        "missing_local": 0,
        "reference_only": 0,
        "metadata_only": 0,
    }
    root = input_path.parent
    for reference in _iter_media_references(archive):
        path_value = reference.get("path")
        url_value = reference.get("url")
        local_exists = False
        if isinstance(path_value, str):
            local_path = (root / Path(path_value)).resolve()
            try:
                local_path.relative_to(root)
            except ValueError:
                local_exists = False
            else:
                local_exists = local_path.is_file()
        if local_exists:
            state = "offline_ready"
        elif path_value:
            state = "missing_local"
        elif url_value and _remote_media_allowed(url_value):
            state = "downloadable"
        elif url_value:
            state = "reference_only"
        else:
            state = "metadata_only"
        counts["total"] += 1
        counts[state] += 1
        host = None
        if url_value:
            host = (urlparse(url_value).hostname or "").lower().rstrip(".") or None
            if host:
                host_counts[host] = host_counts.get(host, 0) + 1
        records.append({**reference, "state": state, "host": host})

    unresolved = counts["missing_local"] + counts["downloadable"] + counts["reference_only"]
    if counts["missing_local"]:
        next_action = "Restore missing local assets, then rerun audit-media and verify the viewer."
    elif counts["downloadable"]:
        next_action = "Review already-recorded approved URLs, then run materialize-media --allow-remote if local copies are permitted."
    elif counts["reference_only"]:
        next_action = "Keep reference-only media as provenance or attach a permitted local copy; the archive will not fetch arbitrary hosts."
    else:
        next_action = "All recorded media references are offline-ready or metadata-only."
    report = {
        "media_audit_version": 1,
        "generated_at": _capture_session_timestamp(),
        "archive": {
            "path": input_path.name,
            "sha256": _sha256(input_path),
            "title": str((archive.get("metadata") or {}).get("title") or input_path.stem),
        },
        "counts": counts,
        "unresolved_count": unresolved,
        "remote_hosts": dict(sorted(host_counts.items())),
        "next_action": next_action,
        "references": records,
    }
    if output_path is not None:
        output_path = output_path.resolve()
        if output_path.parent != input_path.parent:
            raise ValueError("Media audit and archive must be in the same directory")
        if output_path == input_path:
            raise ValueError("Media audit cannot overwrite the archive")
        write_json(output_path, report)
    return report


def materialize_remote_media(
    input_path: Path,
    output_path: Path,
    profile_only: bool = False,
) -> dict[str, int]:
    """Copy allowed remote archive and visible-profile references into local assets.

    This is an explicit, attended step. It only follows URLs already present in
    the archive, plus predictable YouTube thumbnail URLs derived from an
    already-recorded YouTube link; it never logs in, crawls, searches, or
    discovers additional conversation media. ``profile_only`` limits the
    operation to participant/profile pictures so a UI request does not also
    download message attachments or embeds.
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
        if not isinstance(participant, dict):
            continue
        if not participant.get("avatar_path") and participant.get("avatar_ref"):
            path = materialize(
                participant.get("avatar_ref"),
                "avatars",
                str(participant.get("id") or "avatar"),
                f"{participant.get('id') or 'avatar'}.webp",
                "image/webp",
            )
            if path:
                participant["avatar_path"] = path
        profile = participant.get("profile")
        if not isinstance(profile, dict):
            continue
        for field, category, suffix in (
            ("banner", "profile-banners", "banner.png"),
            ("avatar_decoration", "profile-decorations", "decoration.png"),
        ):
            path_key = f"{field}_path"
            ref_key = f"{field}_ref"
            if profile.get(path_key) or not profile.get(ref_key):
                continue
            path = materialize(
                profile.get(ref_key),
                category,
                str(participant.get("id") or field),
                f"{participant.get('id') or 'participant'}-{suffix}",
                "image/png",
            )
            if path:
                profile[path_key] = path
        badges = profile.get("badges")
        if isinstance(badges, list):
            for badge_index, badge in enumerate(badges):
                if not isinstance(badge, dict) or badge.get("icon_path") or not badge.get("icon_ref"):
                    continue
                path = materialize(
                    badge.get("icon_ref"),
                    "profile-badges",
                    str(participant.get("id") or badge_index),
                    f"{participant.get('id') or 'participant'}-badge-{badge_index + 1}.png",
                    "image/png",
                )
                if path:
                    badge["icon_path"] = path
        mutual_friends = profile.get("mutual_friends")
        if isinstance(mutual_friends, list):
            for friend_index, friend in enumerate(mutual_friends):
                if not isinstance(friend, dict) or friend.get("avatar_path") or not friend.get("avatar_ref"):
                    continue
                path = materialize(
                    friend.get("avatar_ref"),
                    "profile-avatars",
                    str(friend.get("id") or friend_index),
                    f"{friend.get('id') or 'mutual-friend'}-avatar.webp",
                    "image/webp",
                )
                if path:
                    friend["avatar_path"] = path

    for message in (archive.get("messages", []) if not profile_only else []):
        if not isinstance(message, dict):
            continue
        for attachment in message.get("attachments", []):
            if not isinstance(attachment, dict) or attachment.get("path") or not attachment.get("url"):
                continue
            mime = str(attachment.get("mime") or mimetypes.guess_type(str(attachment.get("url")))[0] or "")
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
        for collection_name, category, default_mime in (
            ("stickers", "stickers", "image/png"),
            ("custom_emojis", "emojis", "image/png"),
        ):
            collection = message.get(collection_name, [])
            if not isinstance(collection, list):
                continue
            for media_index, media in enumerate(collection):
                if not isinstance(media, dict) or media.get("path") or not media.get("url"):
                    continue
                path = materialize(
                    media.get("url"),
                    category,
                    str(media.get("id") or message.get("id") or media_index),
                    media.get("name") or f"{collection_name[:-1]}-{media_index + 1}",
                    media.get("mime") or default_mime,
                )
                if path:
                    media["path"] = path
        for embed in message.get("embeds", []):
            if not isinstance(embed, dict):
                continue
            if not any(
                embed.get(f"{media_name}_path") or embed.get(f"{media_name}_url")
                for media_name in ("image", "thumbnail", "video", "audio")
            ):
                thumbnail_url = _youtube_thumbnail_url(embed.get("url")) or _youtube_thumbnail_url_from_text(message.get("content"))
                if thumbnail_url:
                    embed["thumbnail_url"] = thumbnail_url
                    embed["thumbnail_source"] = "derived_youtube_thumbnail"
                    embed.setdefault("type", "video")
                    embed.setdefault("site_name", "YouTube")
            for media_name, default_mime in (
                ("image", "image/png"),
                ("thumbnail", "image/png"),
                ("video", "video/mp4"),
                ("audio", "audio/mpeg"),
            ):
                url_key = f"{media_name}_url"
                path_key = f"{media_name}_path"
                if embed.get(path_key) or not embed.get(url_key):
                    continue
                path = materialize(
                    embed.get(url_key),
                    "embeds",
                    str(message.get("id") or "embed"),
                    f"{message.get('id') or 'embed'}-{media_name}",
                    default_mime,
                )
                if path:
                    embed[path_key] = path

    metadata = archive.setdefault("metadata", {})
    source = metadata.get("source")
    if not isinstance(source, dict):
        source = {}
        metadata["source"] = source
    notes = source.setdefault("notes", [])
    note = (
        "Remote Discord profile references were explicitly copied into local archive assets; original URLs were retained."
        if profile_only
        else "Remote Discord CDN and YouTube thumbnail references were explicitly copied into local archive assets; original URLs were retained."
    )
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
            profile = participant.get("profile")
            if isinstance(profile, dict):
                for key in (
                    "banner_path",
                    "banner_ref",
                    "avatar_decoration_path",
                    "avatar_decoration_ref",
                ):
                    collect(profile.get(key))
                badges = profile.get("badges")
                if isinstance(badges, list):
                    for badge in badges:
                        if isinstance(badge, dict):
                            collect(badge.get("icon_path"))
                            collect(badge.get("icon_ref"))
                mutual_friends = profile.get("mutual_friends")
                if isinstance(mutual_friends, list):
                    for friend in mutual_friends:
                        if isinstance(friend, dict):
                            collect(friend.get("avatar_path"))
                            collect(friend.get("avatar_ref"))

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
        for collection_name in ("stickers", "custom_emojis"):
            collection = message.get(collection_name, [])
            if not isinstance(collection, list):
                continue
            for media in collection:
                if isinstance(media, dict):
                    collect(media.get("path"))
                    collect(media.get("preview_path"))
        embeds = message.get("embeds", [])
        if not isinstance(embeds, list):
            embeds = []
        for embed in embeds:
            if isinstance(embed, dict):
                for key, value in embed.items():
                    if key.endswith("_path") or key == "local_path":
                        collect(value)
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
    references = {"archive.json", "app.js", "index.html", "evidence.json"}
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
        required = {"archive.json", "app.js", "index.html", "evidence.json"}
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
            expected_files = {"archive.json", "app.js", "index.html", "evidence.json"} | _asset_references(archive)
            errors.extend(
                f"manifest is missing expected file: {reference}"
                for reference in sorted(expected_files - manifest_paths)
            )
            for reference in sorted(_asset_references(archive)):
                if not (output_dir / reference).is_file():
                    errors.append(f"missing referenced local asset: {reference}")
        elif archive is not None:
            errors.append("archive: archive must be a JSON object")

    evidence_path = output_dir / "evidence.json"
    if not evidence_path.is_file():
        errors.append("evidence.json is missing")
    else:
        errors.extend(f"evidence: {error}" for error in verify_evidence(evidence_path))

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
    evidence = export_evidence(output_dir / "archive.json", output_dir / "evidence.json")

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
    evidence_payload = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    evidence_payload = evidence_payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    (output_dir / "app.js").write_text(
        f"window.__ARCHIVE_DATA__ = {payload};\nwindow.__ARCHIVE_EVIDENCE__ = {evidence_payload};\n\n{app_script}\n",
        encoding="utf-8",
        newline="\n",
    )
    title = str(archive["metadata"]["title"])
    html = template_text.replace("{{ARCHIVE_TITLE}}", title.replace("&", "&amp;").replace("<", "&lt;"))
    html = html.replace("{{ARCHIVE_JSON}}", payload)
    (output_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    write_json(output_dir / "manifest.json", _build_manifest(archive, output_dir, viewer_assets))
    return missing


def _catalog_template_path() -> Path:
    package_template = Path(__file__).resolve().parent / "viewer" / "catalog_template.html"
    source_template = Path(__file__).resolve().parents[2] / "viewer" / "catalog_template.html"
    installed_template = Path(sysconfig.get_path("data")) / "viewer" / "catalog_template.html"
    for candidate in (package_template, source_template, installed_template):
        if candidate.is_file():
            return candidate
    return source_template


def _catalog_script_path(template_path: Path) -> Path:
    return template_path.with_name("catalog_app.js")


def _catalog_slug(value: Any, fallback: str, used: set[str]) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or fallback
    candidate = candidate[:64].strip("-") or fallback
    original = candidate
    suffix = 2
    while candidate in used:
        candidate = f"{original}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _catalog_archive_entry(input_path: Path, archive: dict[str, Any], archive_id: str) -> dict[str, Any]:
    metadata = archive.get("metadata") if isinstance(archive.get("metadata"), dict) else {}
    messages = archive.get("messages") if isinstance(archive.get("messages"), list) else []
    timestamps = sorted(
        str(message.get("timestamp"))
        for message in messages
        if isinstance(message, dict) and message.get("timestamp")
    )
    participants = archive.get("participants") if isinstance(archive.get("participants"), list) else []
    participant_names = [
        str(participant.get("display_name") or participant.get("username") or participant.get("id"))
        for participant in participants
        if isinstance(participant, dict) and (participant.get("display_name") or participant.get("username") or participant.get("id"))
    ]
    capture_range = metadata.get("capture_range") if isinstance(metadata.get("capture_range"), dict) else None
    coverage = metadata.get("coverage") if isinstance(metadata.get("coverage"), dict) else None
    status = str(coverage.get("status")) if coverage else "partial" if capture_range else "unverified"
    complete = bool(coverage.get("complete")) if coverage else False
    try:
        range_count = max(0, int(coverage.get("range_count") or 0)) if coverage else 1 if capture_range else 0
    except (TypeError, ValueError):
        range_count = 0
    title = str(metadata.get("title") or input_path.stem or "Untitled archive")
    kind = str(metadata.get("kind") or "conversation").replace("_", " ")
    return {
        "archive_id": archive_id,
        "title": title,
        "kind": kind,
        "message_count": len(messages),
        "participant_count": len(participants),
        "participant_names": participant_names,
        "oldest_timestamp": timestamps[0] if timestamps else None,
        "newest_timestamp": timestamps[-1] if timestamps else None,
        "coverage_status": status,
        "coverage_complete": complete,
        "coverage_range_count": range_count,
        "source_file": input_path.name,
        "source_sha256": _sha256(input_path),
        "viewer_path": f"archives/{archive_id}/index.html",
    }


def _catalog_message_index_entries(archive: dict[str, Any], archive_entry: dict[str, Any]) -> list[dict[str, Any]]:
    participants = archive.get("participants") if isinstance(archive.get("participants"), list) else []
    participant_names = {
        str(participant.get("id")): str(participant.get("display_name") or participant.get("username") or participant.get("id"))
        for participant in participants
        if isinstance(participant, dict) and participant.get("id") is not None
    }
    messages = archive.get("messages") if isinstance(archive.get("messages"), list) else []
    entries: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("id") is None:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        author_id = str(message.get("author_id") or "")
        entries.append({
            "archive_id": archive_entry["archive_id"],
            "archive_title": archive_entry["title"],
            "viewer_path": archive_entry["viewer_path"],
            "message_id": str(message["id"]),
            "author_name": participant_names.get(author_id, author_id or "Unknown author"),
            "timestamp": message.get("timestamp"),
            "content": content,
        })
    return entries


def _catalog_generated_files(output_dir: Path) -> list[Path]:
    manifest_path = output_dir / "manifest.json"
    return sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path != manifest_path
    )


def _build_catalog_manifest(output_dir: Path) -> dict[str, Any]:
    files = []
    for path in _catalog_generated_files(output_dir):
        files.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "manifest_version": 1,
        "catalog_version": 1,
        "files": files,
    }


def build_catalog(
    input_paths: list[Path],
    output_dir: Path,
    template_path: Path | None = None,
    include_message_index: bool = False,
) -> dict[str, Any]:
    """Build a portable local launcher for multiple normalized archives.

    The catalog is metadata-only by default. ``include_message_index`` is an
    explicit opt-in that adds a local, searchable message index beside it.
    """

    if not input_paths:
        raise ValueError("At least one archive input is required")
    resolved_inputs = [path.resolve() for path in input_paths]
    if len({str(path) for path in resolved_inputs}) != len(resolved_inputs):
        raise ValueError("build-catalog received the same archive more than once")
    output_dir = output_dir.resolve()
    for input_path in resolved_inputs:
        if not input_path.is_file():
            raise FileNotFoundError(f"Archive file does not exist: {input_path}")
        try:
            input_path.relative_to(output_dir)
        except ValueError:
            continue
        raise ValueError("Catalog inputs must not be inside the catalog output directory")

    prepared: list[tuple[Path, dict[str, Any]]] = []
    for input_path in resolved_inputs:
        archive = load_json(input_path)
        errors = validate_archive(archive)
        if errors:
            raise ValueError(f"Archive validation failed for {input_path.name}:\n- " + "\n- ".join(errors))
        prepared.append((input_path, archive))

    template = template_path or _catalog_template_path()
    script_path = _catalog_script_path(template)
    if not template.is_file():
        raise FileNotFoundError(f"Catalog template not found: {template}")
    if not script_path.is_file():
        raise FileNotFoundError(f"Catalog script not found: {script_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    viewer_assets = _copy_viewer_assets(_template_path(), output_dir)
    used_ids: set[str] = set()
    entries: list[dict[str, Any]] = []
    message_index: list[dict[str, Any]] = []
    missing_assets: list[dict[str, Any]] = []
    for index, (input_path, archive) in enumerate(prepared, start=1):
        archive_id = _catalog_slug(archive.get("metadata", {}).get("title") or input_path.stem, f"archive-{index}", used_ids)
        viewer_dir = output_dir / "archives" / archive_id
        missing = build_archive(input_path, viewer_dir)
        if missing:
            missing_assets.append({"archive_id": archive_id, "references": missing})
        entry = _catalog_archive_entry(input_path, archive, archive_id)
        entries.append(entry)
        if include_message_index:
            message_index.extend(_catalog_message_index_entries(archive, entry))

    catalog = {
        "catalog_version": 1,
        "title": "Concordance",
        "generated_at": _capture_session_timestamp(),
        "archives": entries,
    }
    message_index_path = output_dir / "message-index.json"
    if include_message_index:
        catalog["message_index_path"] = "message-index.json"
        catalog["message_index_count"] = len(message_index)
        write_json(
            message_index_path,
            {
                "message_index_version": 1,
                "messages": message_index,
            },
        )
    elif message_index_path.exists():
        message_index_path.unlink()
    write_json(output_dir / "catalog.json", catalog)
    template_text = template.read_text(encoding="utf-8")
    if "{{CATALOG_TITLE}}" not in template_text:
        raise ValueError("Catalog template is missing the CATALOG_TITLE placeholder")
    html = template_text.replace("{{CATALOG_TITLE}}", "Concordance")
    (output_dir / "index.html").write_text(html, encoding="utf-8", newline="\n")
    catalog_payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    catalog_payload = catalog_payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    message_index_payload = json.dumps(message_index, ensure_ascii=False, separators=(",", ":"))
    message_index_payload = message_index_payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    catalog_app = script_path.read_text(encoding="utf-8")
    (output_dir / "catalog.js").write_text(
        f"window.__CONCORDANCE_CATALOG__ = {catalog_payload};\nwindow.__CONCORDANCE_MESSAGE_INDEX__ = {message_index_payload};\n\n{catalog_app}\n",
        encoding="utf-8",
        newline="\n",
    )
    write_json(output_dir / "manifest.json", _build_catalog_manifest(output_dir))
    return {
        "archives": len(entries),
        "entries": entries,
        "missing_assets": missing_assets,
        "output": output_dir,
    }


def verify_catalog(output_dir: Path) -> list[str]:
    """Verify a generated multi-archive catalog and every linked viewer."""

    output_dir = output_dir.resolve()
    errors: list[str] = []
    if not output_dir.is_dir():
        return [f"catalog directory does not exist: {output_dir}"]

    manifest_path = output_dir / "manifest.json"
    manifest_paths: set[str] = set()
    if not manifest_path.is_file():
        errors.append("manifest.json is missing")
        manifest: dict[str, Any] = {}
    else:
        try:
            manifest = load_json(manifest_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            manifest = {}
            errors.append(f"manifest.json could not be read: {error}")
        if not isinstance(manifest, dict):
            errors.append("manifest.json must be a JSON object")
            manifest = {}
    if manifest:
        if manifest.get("manifest_version") != 1:
            errors.append("manifest_version must be 1")
        if manifest.get("catalog_version") != 1:
            errors.append("manifest catalog_version must be 1")
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
                errors.append(f"manifest.files[{index}].path escapes the catalog directory")
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
            if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
                errors.append(f"manifest.files[{index}].sha256 must be a SHA-256 hex digest")
            elif _sha256(path).lower() != expected_hash.lower():
                errors.append(f"hash mismatch: {reference}")
        for required in ("index.html", "catalog.js", "catalog.json"):
            if required not in seen_paths:
                errors.append(f"manifest is missing required file: {required}")

    catalog_path = output_dir / "catalog.json"
    if not catalog_path.is_file():
        errors.append("catalog.json is missing")
        catalog: dict[str, Any] = {}
    else:
        try:
            catalog = load_json(catalog_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            catalog = {}
            errors.append(f"catalog.json could not be read: {error}")
    if catalog:
        if catalog.get("catalog_version") != 1:
            errors.append("catalog_version must be 1")
        archives = catalog.get("archives")
        if not isinstance(archives, list):
            errors.append("catalog.archives must be an array")
            archives = []
        archive_ids: set[str] = set()
        viewer_paths: set[str] = set()
        for index, entry in enumerate(archives):
            if not isinstance(entry, dict):
                errors.append(f"catalog.archives[{index}] must be an object")
                continue
            archive_id = entry.get("archive_id")
            if not isinstance(archive_id, str) or not archive_id or _normalise_local_reference(archive_id) != archive_id:
                errors.append(f"catalog.archives[{index}].archive_id must be a safe identifier")
            elif archive_id in archive_ids:
                errors.append(f"catalog.archives[{index}].archive_id duplicates {archive_id!r}")
            else:
                archive_ids.add(archive_id)
            viewer_path = entry.get("viewer_path")
            if not isinstance(viewer_path, str) or _normalise_local_reference(viewer_path) != viewer_path:
                errors.append(f"catalog.archives[{index}].viewer_path must be a safe relative path")
                continue
            if not viewer_path.startswith("archives/") or not viewer_path.endswith("/index.html"):
                errors.append(f"catalog.archives[{index}].viewer_path must point into archives/")
                continue
            if viewer_path in viewer_paths:
                errors.append(f"catalog.archives[{index}].viewer_path duplicates {viewer_path!r}")
                continue
            viewer_paths.add(viewer_path)
            viewer_dir = (output_dir / viewer_path).parent.resolve()
            try:
                viewer_dir.relative_to(output_dir)
            except ValueError:
                errors.append(f"catalog.archives[{index}].viewer_path escapes the catalog directory")
                continue
            if not (viewer_dir / "index.html").is_file():
                errors.append(f"catalog archive viewer is missing: {viewer_path}")
                continue
            errors.extend(f"{archive_id}: {error}" for error in verify_build(viewer_dir))
        message_index_reference = catalog.get("message_index_path")
        if message_index_reference is not None:
            if not isinstance(message_index_reference, str) or _normalise_local_reference(message_index_reference) != message_index_reference:
                errors.append("catalog.message_index_path must be a safe relative path")
            else:
                message_index_path = (output_dir / message_index_reference).resolve()
                try:
                    message_index_path.relative_to(output_dir)
                except ValueError:
                    errors.append("catalog.message_index_path escapes the catalog directory")
                    message_index_path = None
                if message_index_path is not None:
                    if not message_index_path.is_file():
                        errors.append(f"catalog message index is missing: {message_index_reference}")
                    else:
                        try:
                            message_index = load_json(message_index_path)
                        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                            message_index = {}
                            errors.append(f"message index could not be read: {error}")
                        if message_index:
                            if message_index.get("message_index_version") != 1:
                                errors.append("message_index_version must be 1")
                            indexed_messages = message_index.get("messages")
                            if not isinstance(indexed_messages, list):
                                errors.append("message index messages must be an array")
                            else:
                                indexed_count = catalog.get("message_index_count")
                                if isinstance(indexed_count, bool) or not isinstance(indexed_count, int) or indexed_count < 0:
                                    errors.append("catalog.message_index_count must be a non-negative integer")
                                elif indexed_count != len(indexed_messages):
                                    errors.append("catalog message_index_count does not match the message index")
                                known_archive_ids = {
                                    entry.get("archive_id")
                                    for entry in archives
                                    if isinstance(entry, dict)
                                }
                                for index, item in enumerate(indexed_messages):
                                    if not isinstance(item, dict):
                                        errors.append(f"message index entry {index + 1} must be an object")
                                        continue
                                    if item.get("archive_id") not in known_archive_ids:
                                        errors.append(f"message index entry {index + 1} references an unknown archive")
                                    if not isinstance(item.get("message_id"), str) or not item["message_id"]:
                                        errors.append(f"message index entry {index + 1}.message_id must be a non-empty string")
                                    if not isinstance(item.get("content"), str):
                                        errors.append(f"message index entry {index + 1}.content must be a string")
        elif (output_dir / "message-index.json").exists():
            errors.append("message-index.json exists but catalog.message_index_path is missing")
    expected_files = {"index.html", "catalog.js", "catalog.json"}
    expected_files.update(
        reference
        for reference in manifest_paths
        if reference.startswith("archives/") or reference.startswith("assets/")
    )
    errors.extend(
        f"manifest is missing expected file: {reference}"
        for reference in sorted(expected_files - manifest_paths)
    )
    return errors
