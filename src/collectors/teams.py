import html as html_lib
import re
from dataclasses import dataclass
from datetime import date
from src.collectors.graph_client import GraphClient


@dataclass
class TeamsMessage:
    chat_topic: str
    sender: str
    created_at: str
    content: str
    chat_type: str


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def collect_teams_messages(client: GraphClient, target_date: date) -> list[TeamsMessage]:
    d = target_date.isoformat()
    start = f"{d}T00:00:00Z"
    end = f"{d}T23:59:59Z"

    chats_data = client.get(
        "/me/chats",
        **{"$select": "id,chatType,topic", "$top": "30"},
    )

    messages: list[TeamsMessage] = []
    for chat in chats_data.get("value", []):
        chat_id = chat["id"]
        topic = chat.get("topic") or chat.get("chatType", "chat")

        try:
            msgs_data = client.get(
                f"/me/chats/{chat_id}/messages",
                **{
                    "$filter": f"createdDateTime ge {start} and createdDateTime le {end}",
                    "$select": "from,createdDateTime,body",
                    "$top": "25",
                },
            )
        except Exception:
            continue

        for msg in msgs_data.get("value", []):
            body = msg.get("body", {})
            raw = body.get("content", "")
            content = _strip_html(raw) if body.get("contentType") == "html" else raw
            content = content[:400].strip()

            if not content:
                continue

            from_field = msg.get("from") or {}
            sender = (
                (from_field.get("user") or {}).get("displayName")
                or (from_field.get("application") or {}).get("displayName")
                or "unknown"
            )

            messages.append(TeamsMessage(
                chat_topic=topic,
                sender=sender,
                created_at=msg.get("createdDateTime", ""),
                content=content,
                chat_type=chat.get("chatType", "chat"),
            ))

    return messages
