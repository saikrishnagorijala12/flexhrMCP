import html as html_lib
import re
from dataclasses import dataclass
from datetime import date
from src.collectors.graph_client import GraphClient


@dataclass
class Email:
    subject: str
    sender: str
    received_at: str
    body_preview: str
    is_sent: bool = False


def collect_emails(client: GraphClient, target_date: date) -> list[Email]:
    d = target_date.isoformat()
    date_filter = f"receivedDateTime ge {d}T00:00:00Z and receivedDateTime le {d}T23:59:59Z"
    sent_filter = f"sentDateTime ge {d}T00:00:00Z and sentDateTime le {d}T23:59:59Z"

    received_data = client.get(
        "/me/messages",
        **{
            "$filter": date_filter,
            "$select": "subject,from,receivedDateTime,bodyPreview",
            "$top": "50",
            "$orderby": "receivedDateTime desc",
        },
    )

    sent_data = client.get(
        "/me/mailFolders/SentItems/messages",
        **{
            "$filter": sent_filter,
            "$select": "subject,toRecipients,sentDateTime,bodyPreview",
            "$top": "30",
        },
    )

    emails = []
    for msg in received_data.get("value", []):
        emails.append(Email(
            subject=msg.get("subject", ""),
            sender=msg.get("from", {}).get("emailAddress", {}).get("address", ""),
            received_at=msg.get("receivedDateTime", ""),
            body_preview=msg.get("bodyPreview", "")[:200],
        ))

    for msg in sent_data.get("value", []):
        to_list = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        ]
        emails.append(Email(
            subject=msg.get("subject", ""),
            sender="(me)",
            received_at=msg.get("sentDateTime", ""),
            body_preview=msg.get("bodyPreview", "")[:200],
            is_sent=True,
        ))

    return emails
