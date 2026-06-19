import base64
import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from src.collectors.graph_client import GraphClient


@dataclass
class OneNotePage:
    title: str
    notebook: str
    section: str
    last_modified: str
    text_content: str
    images: list[dict] = field(default_factory=list)  # [{"b64": str, "mime": str}]


def _html_to_text(html_content: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html_content, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _guess_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/png"


def collect_onenote_pages(client: GraphClient, target_date: date) -> list[OneNotePage]:
    # Look back 2 days to capture notes written the morning after
    lookback = (target_date - timedelta(days=1)).isoformat()

    pages_data = client.get(
        "/me/onenote/pages",
        **{
            "$filter": f"lastModifiedDateTime ge {lookback}T00:00:00Z",
            "$select": "id,title,parentNotebook,parentSection,lastModifiedDateTime,contentUrl",
            "$top": "20",
            "$orderby": "lastModifiedDateTime desc",
        },
    )

    pages: list[OneNotePage] = []
    for page in pages_data.get("value", []):
        content_url = page.get("contentUrl", "")
        if not content_url:
            continue

        try:
            html_bytes = client.get_bytes(content_url)
            html_content = html_bytes.decode("utf-8", errors="ignore")
        except Exception:
            continue

        # Find image src URLs pointing to Graph API resources
        img_urls = re.findall(
            r'src="(https://graph\.microsoft\.com[^"]+/content)"',
            html_content,
        )

        images: list[dict] = []
        for url in img_urls[:4]:  # max 4 images per page
            try:
                img_bytes = client.get_bytes(url)
                images.append({
                    "b64": base64.b64encode(img_bytes).decode(),
                    "mime": _guess_mime(img_bytes),
                })
            except Exception:
                continue

        pages.append(OneNotePage(
            title=page.get("title", "Untitled"),
            notebook=page.get("parentNotebook", {}).get("displayName", ""),
            section=page.get("parentSection", {}).get("displayName", ""),
            last_modified=page.get("lastModifiedDateTime", ""),
            text_content=_html_to_text(html_content)[:3000],
            images=images,
        ))

    return pages
