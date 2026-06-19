import base64
import difflib
import re
from datetime import datetime
from pathlib import Path

from src.collectors.onedrive import DriveFile, _extract_dates, _guess_mime, _read_docx_bytes

_SUPPORTED_TEXT_EXTS = {".txt", ".md", ".log", ".rst", ".text"}
_SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def collect_local_notes(
    folder: str,
) -> list[DriveFile]:
    """Read activity files from a local folder path.
    All files are returned — the AI extracts entries for the requested date from content.
    """
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Notes folder not found: {root}")

    files: list[DriveFile] = []

    for path in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue

        ext = path.suffix.lower()
        is_image = ext in _SUPPORTED_IMAGE_EXTS
        is_docx = ext == ".docx"

        if ext not in _SUPPORTED_TEXT_EXTS and not is_docx and not is_image:
            continue

        stat = path.stat()
        last_modified = _fmt(stat.st_mtime)
        created_at = _fmt(stat.st_ctime)

        raw_bytes = path.read_bytes()

        if is_image:
            files.append(DriveFile(
                filename=path.name,
                folder_path=folder,
                created_at=created_at,
                last_modified_at=last_modified,
                content="",
                images=[{"b64": base64.b64encode(raw_bytes).decode(), "mime": _guess_mime(raw_bytes, ext)}],
            ))
            continue

        text = _read_docx_bytes(raw_bytes) if is_docx else raw_bytes.decode("utf-8", errors="ignore")

        files.append(DriveFile(
            filename=path.name,
            folder_path=folder,
            created_at=created_at,
            last_modified_at=last_modified,
            content=text[:3000],
            detected_dates=_extract_dates(text),
        ))

    return files
