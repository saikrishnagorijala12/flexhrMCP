import base64
import difflib
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date

from src.collectors.graph_client import GraphClient

_DATE_PATTERNS = [
    r'\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b',
    r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b',
    r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b',
    r'\b((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2})\b',
]

_SUPPORTED_TEXT_EXTS = {".txt", ".md", ".log", ".rst", ".text"}
_SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


@dataclass
class DriveFile:
    filename: str
    folder_path: str
    created_at: str          # ISO datetime from Graph metadata
    last_modified_at: str    # ISO datetime from Graph metadata
    content: str             # extracted text (empty for pure-image files)
    images: list[dict] = field(default_factory=list)   # [{"b64": str, "mime": str}]
    detected_dates: list[str] = field(default_factory=list)
    last_change_summary: str = ""


def _extract_dates(text: str) -> list[str]:
    found = []
    for pattern in _DATE_PATTERNS:
        found.extend(re.findall(pattern, text, re.IGNORECASE))
    return list(dict.fromkeys(found))


def _guess_mime(data: bytes, ext: str) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    mime_map = {".webp": "image/webp", ".bmp": "image/bmp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    return mime_map.get(ext, "image/png")


def _read_docx_bytes(data: bytes) -> str:
    """Extract plain text from a .docx ZIP archive without python-docx."""
    try:
        import io
        import zipfile
        import xml.etree.ElementTree as ET

        z = zipfile.ZipFile(io.BytesIO(data))
        xml_content = z.read("word/document.xml")
        tree = ET.fromstring(xml_content)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines = []
        for para in tree.findall(".//w:p", ns):
            texts = [t.text or "" for t in para.findall(".//w:t", ns)]
            lines.append("".join(texts))
        return "\n".join(lines).strip()
    except Exception:
        return ""


def _diff_summary(old_text: str, new_text: str) -> str:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=0))
    added = [l[1:].strip() for l in diff if l.startswith("+") and not l.startswith("+++")]
    removed = [l[1:].strip() for l in diff if l.startswith("-") and not l.startswith("---")]
    parts = []
    if added:
        sample = "; ".join(filter(None, added[:5]))
        parts.append(f"Added: {sample}")
    if removed:
        sample = "; ".join(filter(None, removed[:5]))
        parts.append(f"Removed: {sample}")
    return " | ".join(parts) if parts else "No text changes detected"


def _graph_url(path: str) -> str:
    return f"https://graph.microsoft.com/v1.0{path}"


def collect_onedrive_files(
    client: GraphClient,
    folder_path: str,
) -> list[DriveFile]:
    """Collect activity files from a OneDrive folder.
    All files are returned — the AI extracts entries for the requested date from content.
    """
    if not folder_path:
        return []

    folder_path = folder_path.strip("/")
    encoded = urllib.parse.quote(folder_path, safe="/")

    children = client.get(
        f"/me/drive/root:/{encoded}:/children",
        **{
            "$select": "id,name,createdDateTime,lastModifiedDateTime,file",
            "$top": "50",
        },
    )

    files: list[DriveFile] = []

    for item in children.get("value", []):
        if "file" not in item:
            continue

        name: str = item.get("name", "")
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        is_docx = ext == ".docx"
        is_image = ext in _SUPPORTED_IMAGE_EXTS

        if ext not in _SUPPORTED_TEXT_EXTS and not is_docx and not is_image:
            continue

        last_modified = item.get("lastModifiedDateTime", "")
        created_at = item.get("createdDateTime", "")
        item_id: str = item["id"]

        try:
            raw_bytes = client.get_bytes(_graph_url(f"/me/drive/items/{item_id}/content"))
        except Exception:
            continue

        if is_image:
            files.append(DriveFile(
                filename=name,
                folder_path=folder_path,
                created_at=created_at,
                last_modified_at=last_modified,
                content="",
                images=[{"b64": base64.b64encode(raw_bytes).decode(), "mime": _guess_mime(raw_bytes, ext)}],
                detected_dates=[],
                last_change_summary="",
            ))
            continue

        text = _read_docx_bytes(raw_bytes) if is_docx else raw_bytes.decode("utf-8", errors="ignore")

        # Attempt to retrieve version history for change diffing
        last_change_summary = ""
        try:
            versions_resp = client.get(
                f"/me/drive/items/{item_id}/versions",
                **{"$select": "id,lastModifiedDateTime", "$top": "2"},
            )
            versions = versions_resp.get("value", [])
            if len(versions) >= 2:
                prev_id = versions[1]["id"]
                prev_url = _graph_url(f"/me/drive/items/{item_id}/versions/{prev_id}/content")
                try:
                    prev_bytes = client.get_bytes(prev_url)
                    prev_text = _read_docx_bytes(prev_bytes) if is_docx else prev_bytes.decode("utf-8", errors="ignore")
                    last_change_summary = _diff_summary(prev_text, text)
                except Exception:
                    last_change_summary = "Previous version content unavailable"
        except Exception:
            pass

        files.append(DriveFile(
            filename=name,
            folder_path=folder_path,
            created_at=created_at,
            last_modified_at=last_modified,
            content=text[:3000],
            detected_dates=_extract_dates(text),
            last_change_summary=last_change_summary,
        ))

    return files
