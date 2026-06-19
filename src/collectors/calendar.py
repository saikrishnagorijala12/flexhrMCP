from dataclasses import dataclass, field
from datetime import date, datetime
from src.collectors.graph_client import GraphClient


@dataclass
class CalendarEvent:
    subject: str
    start: str
    end: str
    duration_hours: float
    organizer: str
    attendees: list[str]
    body_preview: str
    is_online_meeting: bool


def collect_calendar_events(client: GraphClient, target_date: date) -> list[CalendarEvent]:
    start = f"{target_date.isoformat()}T00:00:00Z"
    end = f"{target_date.isoformat()}T23:59:59Z"

    data = client.get(
        "/me/calendarView",
        startDateTime=start,
        endDateTime=end,
        **{
            "$select": "subject,start,end,organizer,attendees,bodyPreview,isOnlineMeeting",
            "$top": "50",
        },
    )

    events = []
    for item in data.get("value", []):
        try:
            s = item["start"]["dateTime"].replace("Z", "+00:00")
            e = item["end"]["dateTime"].replace("Z", "+00:00")
            start_dt = datetime.fromisoformat(s)
            end_dt = datetime.fromisoformat(e)
            duration = round((end_dt - start_dt).total_seconds() / 3600, 2)
        except (KeyError, ValueError):
            duration = 0.0

        attendees = [
            a.get("emailAddress", {}).get("address", "")
            for a in item.get("attendees", [])
        ]

        events.append(CalendarEvent(
            subject=item.get("subject", ""),
            start=item.get("start", {}).get("dateTime", ""),
            end=item.get("end", {}).get("dateTime", ""),
            duration_hours=duration,
            organizer=item.get("organizer", {}).get("emailAddress", {}).get("address", ""),
            attendees=attendees[:5],
            body_preview=item.get("bodyPreview", "")[:200],
            is_online_meeting=item.get("isOnlineMeeting", False),
        ))

    return events
