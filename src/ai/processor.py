import json
from datetime import date
from openai import OpenAI
from src.collectors.calendar import CalendarEvent
from src.collectors.emails import Email
from src.collectors.teams import TeamsMessage
from src.collectors.onenote import OneNotePage
from src.collectors.onedrive import DriveFile


_SYSTEM = """\
You are a timesheet assistant. Analyse a person's work day from their calendar events, emails,
Teams messages, OneNote notes, and OneDrive activity files, then produce accurate timesheet entries.

Rules:
1. Only log hours clearly evidenced by the data — never invent or pad entries.
2. Calendar events are the strongest signal; use their duration for hours.
3. Emails and Teams messages are BOTH context signals AND time signals:
   - Each work-related email sent or received = ~10 min; a thread of 5+ back-and-forth emails = ~30–60 min.
   - Teams chat activity on a topic thread = 15–30 min per active thread; a long back-and-forth = up to 1 h.
   - Group into a single correspondence entry per project. Skip automated/promotional/notification emails.
4. OneDrive/OneNote files (typed notes or photos of handwritten notes) are high-confidence sources.
   OCR any text visible in images. If no date is in the content, use the file's last-modified date.
5. Manual notes from the employee are high-confidence — treat as self-reported time logs.
6. Total hours must not exceed a normal workday (~8 h). Do NOT pad to fill it.
7. Descriptions should be 1-2 concise factual sentences (they appear in the timesheet Note field).
8. Return entries in order of most to least hours.
9. ACTIVITY TYPE — CRITICAL RULES:
   a. You MUST use the EXACT activity type name from the "Available Activity Types" list. Never invent names.
   b. Common mappings (use these exact strings):
      - Coding / building / development work  → "Software Development"
      - Testing / QA work                     → "QA & Testing"
      - Client calls / client meetings         → "Client Coordination"
      - Scrum / standup / sprint meetings      → "Scrum & Sprint Execution"
      - AI or ML related work                 → "AI/ML Activities"
      - DevOps / cloud / infra work           → "DevOps & Cloud"
      - Design work                           → "UI/UX Design"
      - Research or data analysis             → "Research/Data Analysis"
      - Knowledge transfer / self-learning     → "Continuous Learning and Training"
      - Internal team meetings (non-client)   → "Internal Meetings"
   c. Never use "Unassigned / Free Time". Use "Continuous Learning and Training" if no work is evidenced.
10. PROJECT ASSIGNMENT — CRITICAL RULES:
    a. All client delivery work (coding, testing, client calls, scrums, DevOps, design, etc.)
       must go to the SPECIFIC CLIENT PROJECT code (e.g. PROJ-0034, PROJ-0055), NOT to
       "Talent Support & Engagement Hub".
    b. "Talent Support & Engagement Hub" is only for: HR activities, company-wide learning,
       holidays, compliance, mentoring, and general internal admin — NOT for client work.
    c. Infer the correct client project from context (meeting titles, email subjects, notes content).
"""

_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_timesheet_entries",
        "description": "Submit the processed timesheet entries",
        "parameters": {
            "type": "object",
            "required": ["entries"],
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["hours", "project", "activity_type", "description", "confidence"],
                        "properties": {
                            "hours":         {"type": "number", "description": "Total hours spent (e.g. 2.5)"},
                            "project":       {"type": "string"},
                            "activity_type": {"type": "string", "description": "Must be one of the available activity types"},
                            "description":   {"type": "string"},
                            "confidence":    {"type": "string", "enum": ["high", "medium", "low"]},
                            "sources":       {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "notes": {
                    "type": "string",
                    "description": "Observations or ambiguities the user should review",
                },
            },
        },
    },
}


def _build_content(
    target_date: date,
    events: list[CalendarEvent],
    emails: list[Email],
    messages: list[TeamsMessage],
    pages: list[OneNotePage],
    activity_types: list[str],
    projects: list[str],
    manual_notes: str = "",
    drive_files: list[DriveFile] | None = None,
) -> list[dict]:
    """Build the OpenAI-format content array (text + image_url blocks)."""
    lines = [
        f"## Date: {target_date.strftime('%A, %B %d, %Y')}",
        f"## Available Activity Types: {', '.join(activity_types)}",
        f"## Known Projects: {', '.join(projects) if projects else 'infer from context'}",
    ]

    if events:
        lines.append("\n## Calendar Events")
        for e in events:
            s = e.start[:16].replace("T", " ")
            en = e.end[:16].replace("T", " ")
            lines.append(
                f"- [{s} → {en}] {e.subject} ({e.duration_hours}h)"
                + (f" | {e.body_preview[:120]}" if e.body_preview else "")
            )

    if emails:
        lines.append("\n## Emails")
        for em in emails[:25]:
            tag = "SENT" if em.is_sent else "RECV"
            lines.append(f"- [{tag}] {em.subject} | from {em.sender}")
            if em.body_preview:
                lines.append(f"  {em.body_preview[:160]}")

    if messages:
        lines.append("\n## Teams Messages")
        for m in messages[:40]:
            ts = m.created_at[:16]
            lines.append(f"- [{ts}] [{m.chat_topic}] {m.sender}: {m.content[:250]}")

    text_pages = [p for p in pages if p.text_content]
    if text_pages:
        lines.append("\n## OneNote Pages")
        for p in text_pages[:5]:
            lines.append(f"### {p.title}  ({p.notebook} › {p.section})")
            lines.append(p.text_content[:2000])

    if drive_files:
        lines.append("\n## OneDrive Activity Files")
        for f in drive_files:
            lines.append(f"### {f.filename}  (created: {f.created_at[:10]}, last modified: {f.last_modified_at[:10]})")
            if f.images:
                lines.append(f"  [Image file — OCR the photo above labelled '{f.filename}']")
                lines.append(f"  No dates in content — use file last-modified: {f.last_modified_at[:10]}")
            else:
                date_info = (
                    f"Dates found in content: {', '.join(f.detected_dates)}"
                    if f.detected_dates
                    else f"No dates in content — use file last-modified: {f.last_modified_at[:10]}"
                )
                lines.append(f"  {date_info}")
                if f.last_change_summary:
                    lines.append(f"  Last change: {f.last_change_summary}")
                lines.append(f.content[:2000])

    if manual_notes:
        lines.append(
            f"\n## Manual notes from employee (high-confidence — treat as self-reported time log)\n{manual_notes}"
        )

    lines.append("\n\nBased on the above, generate timesheet entries for this day.")
    prompt_text = "\n".join(lines)

    # Build content blocks: images first, then the text prompt
    content: list[dict] = []
    for p in pages:
        for img in p.images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"},
            })
            content.append({"type": "text", "text": f"(Image from OneNote page: {p.title})"})

    for f in (drive_files or []):
        for img in f.images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"},
            })
            content.append({"type": "text", "text": f"(Photo/image from OneDrive file: {f.filename}, last modified: {f.last_modified_at[:10]})"})

    content.append({"type": "text", "text": prompt_text})
    return content


def make_openrouter_client(api_key: str) -> OpenAI:
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


def process_with_openrouter(
    client: OpenAI,
    model: str,
    target_date: date,
    events: list[CalendarEvent],
    emails: list[Email],
    messages: list[TeamsMessage],
    pages: list[OneNotePage],
    activity_types: list[str],
    projects: list[str],
    manual_notes: str = "",
    drive_files: list[DriveFile] | None = None,
) -> dict:
    content = _build_content(
        target_date, events, emails, messages, pages, activity_types, projects,
        manual_notes=manual_notes,
        drive_files=drive_files,
    )

    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": content},
        ],
        tools=[_TOOL],
        tool_choice={"type": "function", "function": {"name": "submit_timesheet_entries"}},
    )

    msg = response.choices[0].message
    if msg.tool_calls:
        return json.loads(msg.tool_calls[0].function.arguments)

    return {"entries": [], "notes": "No entries could be extracted from the available data."}
