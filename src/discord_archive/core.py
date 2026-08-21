from __future__ import annotations

import json
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
_MISSING = object()


def load_json(path: Path) -> dict[str, Any]:
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
    candidate = value.strip().replace("Z", "+00:00")
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
            errors.append(
                f"messages[{index}].timestamp must be an ISO-8601 timestamp with a timezone"
            )
        if not isinstance(message.get("content", ""), str):
            errors.append(f"messages[{index}].content must be a string")
        if "attachments" in message and not isinstance(message["attachments"], list):
            errors.append(f"messages[{index}].attachments must be an array")

    return errors


def _first(record: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return default


def _normalise_attachment(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {
            "name": Path(value).name or "attachment",
            "url": value if value.startswith(("http://", "https://")) else None,
            "path": value if not value.startswith(("http://", "https://")) else None,
            "mime": mimetypes.guess_type(value)[0] or "application/octet-stream",
        }
    if isinstance(value, dict):
        path = _first(value, "path", "local_path", "file", default=None)
        url = _first(value, "url", "proxy_url", default=None)
        name = _first(value, "name", "filename", default=None)
        return {
            "name": name or (Path(path).name if isinstance(path, str) else "attachment"),
            "path": path,
            "url": url,
            "mime": _first(value, "mime", "content_type", default=None)
            or (mimetypes.guess_type(str(path))[0] if path else None)
            or "application/octet-stream",
            "size_bytes": _first(value, "size_bytes", "size", default=None),
        }
    return {"name": "attachment", "path": None, "url": None, "mime": "application/octet-stream"}


def _normalise_record(record: dict[str, Any], channel_id: str, index: int) -> dict[str, Any] | None:
    message_id = _first(record, "id", "ID", "message_id", "Message ID", default=None)
    timestamp = normalise_timestamp(
        _first(record, "timestamp", "Timestamp", "created_at", "Date", default=None)
    )
    if message_id is None or timestamp is None:
        return None
    content = _first(record, "content", "Contents", "message", "Message", default="")
    if not isinstance(content, str):
        content = str(content)
    raw_attachments = _first(record, "attachments", "Attachments", default=[])
    if not isinstance(raw_attachments, list):
        raw_attachments = [raw_attachments]
    return {
        "id": str(message_id),
        "author_id": "me",
        "timestamp": timestamp,
        "content": content,
        "channel_id": channel_id,
        "attachments": [_normalise_attachment(item) for item in raw_attachments],
        "reactions": [],
        "embeds": [],
        "source_index": index,
    }


def _records_from_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
    elif isinstance(value, dict):
        nested = value.get("messages")
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    yield item
        elif any(key in value for key in ("id", "ID", "timestamp", "Timestamp")):
            yield value


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
    for json_path in sorted((source_dir / "messages").rglob("*.json")) if (source_dir / "messages").exists() else []:
        try:
            with json_path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        channel_id = json_path.parent.name
        for index, record in enumerate(_records_from_json(value)):
            normalised = _normalise_record(record, channel_id, index)
            if normalised:
                records.append(normalised)

    records.sort(key=lambda item: (item["timestamp"], item["id"]))
    channel_ids = sorted({record["channel_id"] for record in records})
    title = "Discord Data Package"
    if len(channel_ids) == 1:
        title = f"Discord DM {channel_ids[0]}"
    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
                "source_directory": str(source_dir),
                "notes": [
                    "This import contains messages available in the supplied package.",
                    "Discord Data Packages contain messages sent by the requesting account.",
                    "Remote attachment URLs are preserved as provenance and are not downloaded.",
                ],
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
                if key not in {"channel_id", "source_index"}
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
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            return
        references.add(path.as_posix())

    for participant in archive.get("participants", []):
        if isinstance(participant, dict):
            collect(participant.get("avatar_path"))
            collect(participant.get("avatar_ref"))

    for message in archive.get("messages", []):
        if not isinstance(message, dict):
            continue
        collect(message.get("avatar_ref"))
        for attachment in message.get("attachments", []):
            if isinstance(attachment, dict):
                collect(attachment.get("path"))
                collect(attachment.get("local_path"))
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


def _template_path() -> Path:
    return Path(__file__).resolve().parents[2] / "viewer" / "template.html"


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
    return missing
