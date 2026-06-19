#!/usr/bin/env python3
"""FlexHR Timesheet Agent.

Reads your Outlook calendar, emails, Teams messages, and OneNote notes for a
given date, then uses Claude to produce timesheet entries. You review and
approve them before they are submitted to Frappe HRMS (FlexHR).

Usage:
    python agent.py                     # yesterday (default)
    python agent.py --date 2025-06-13   # specific date
    python agent.py --today             # today
"""
import re
from datetime import date, timedelta

import typer
from dotenv import load_dotenv
from rich.panel import Panel
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn

load_dotenv()

from src.ai.processor import make_openrouter_client, process_with_openrouter
from src.collectors.calendar import collect_calendar_events
from src.collectors.emails import collect_emails
from src.collectors.graph_client import GraphClient
from src.collectors.onenote import collect_onenote_pages
from src.collectors.onedrive import collect_onedrive_files
from src.collectors.local_notes import collect_local_notes
from src.collectors.teams import collect_teams_messages
from src.config import Config
from src.flexhr.client import FlexHRClient
from src.ui.preview import console, preview_and_approve

app = typer.Typer(add_completion=False, help=__doc__)


@app.command("fields")
def show_fields():
    """Print all field names on the Timesheet Detail child table."""
    from rich.table import Table
    from rich import box as rbox
    config = Config.from_env()
    with FlexHRClient(config) as flexhr:
        fields = flexhr.get_timesheet_detail_fields()
    t = Table(title="Timesheet Detail fields", box=rbox.SIMPLE)
    t.add_column("fieldname", style="cyan")
    t.add_column("label")
    t.add_column("type")
    t.add_column("required")
    for f in fields:
        t.add_row(f["fieldname"], f["label"] or "", f["fieldtype"] or "",
                  "yes" if f["reqd"] else "")
    console.print(t)


def _run(label: str, fn, fallback, warnings: list):
    try:
        return fn()
    except Exception as exc:
        warnings.append((label, str(exc)))
        return fallback


def _friendly_warning(label: str, msg: str) -> str:
    if "401" in msg and "empty" in msg:
        return f"[yellow]⚠[/yellow]  {label}: no Exchange/SharePoint license on this tenant (skipped)"
    if "license" in msg.lower():
        return f"[yellow]⚠[/yellow]  {label}: {msg.split(':')[-1].strip()} (skipped)"
    if label == "Teams" and ("403" in msg or "No authorization" in msg):
        return f"[dim]ℹ  Teams: not available on personal Microsoft accounts (skipped)[/dim]"
    if "SPO" in msg or "SharePoint" in msg:
        return f"[dim]ℹ  {label}: SharePoint not available on this account (skipped)[/dim]"
    return f"[yellow]⚠[/yellow]  {label}: {msg}"


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    date_str: str = typer.Option(None, "--date", "-d", metavar="YYYY-MM-DD",
                                 help="Date to process (default: yesterday)"),
    today: bool = typer.Option(False, "--today", help="Use today instead of yesterday"),
    wfh: bool = typer.Option(False, "--wfh", help="Mark all entries as Work From Home"),
):
    if ctx.invoked_subcommand is not None:
        return
    if today:
        target_date = date.today()
    elif date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            console.print(f"[red]Invalid date '{date_str}'. Use YYYY-MM-DD format.[/red]")
            raise typer.Exit(1)
    else:
        target_date = date.today() - timedelta(days=1)

    console.print(
        f"\n[bold cyan]FlexHR Timesheet Agent[/bold cyan]  —  "
        f"[bold]{target_date.strftime('%A, %B %d, %Y')}[/bold]\n"
    )

    try:
        config = Config.from_env()
    except KeyError as exc:
        console.print(f"[red]Missing environment variable: {exc}[/red]")
        console.print("[dim]Copy .env.example to .env and fill in your credentials.[/dim]")
        raise typer.Exit(1)

    if wfh:
        config.flexhr_work_status = "Working From Home"
        console.print("[dim]Work status: Work From Home[/dim]")

    warnings: list[tuple[str, str]] = []

    with FlexHRClient(config) as flexhr, GraphClient(config) as graph:
        ai_client = make_openrouter_client(config.openrouter_api_key)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:

            t = progress.add_task("Fetching FlexHR metadata…")
            activity_types = flexhr.get_activity_types()
            projects_raw = flexhr.get_projects()
            # Lookup: any known alias (human name or ID) → Frappe document name
            project_lookup: dict[str, str] = {}
            for p in projects_raw:
                pid = p["name"]
                project_lookup[pid.lower()] = pid
                pname = p.get("project_name") or ""
                if pname and pname.lower() != pid.lower():
                    project_lookup[pname.lower()] = pid
            # Display list for AI: "Apollo Project (PROJ-0034)"
            projects: list[str] = [
                f"{p['project_name']} ({p['name']})" if p.get("project_name") and p.get("project_name") != p["name"]
                else p["name"]
                for p in projects_raw
            ]
            progress.update(t, completed=True,
                            description=f"[green]✓[/green] FlexHR: {len(activity_types)} activity types, {len(projects_raw)} projects")

            t = progress.add_task("Reading Outlook calendar…")
            events = _run("Calendar", lambda: collect_calendar_events(graph, target_date), [], warnings)
            progress.update(t, completed=True,
                            description=f"[green]✓[/green] Calendar: {len(events)} events")

            t = progress.add_task("Reading Outlook emails…")
            emails = _run("Emails", lambda: collect_emails(graph, target_date), [], warnings)
            progress.update(t, completed=True,
                            description=f"[green]✓[/green] Emails: {len(emails)} messages")

            t = progress.add_task("Reading Teams messages…")
            messages = _run("Teams", lambda: collect_teams_messages(graph, target_date), [], warnings)
            progress.update(t, completed=True,
                            description=f"[green]✓[/green] Teams: {len(messages)} messages")

            t = progress.add_task("Reading OneNote pages…")
            pages = _run("OneNote", lambda: collect_onenote_pages(graph, target_date), [], warnings)
            progress.update(t, completed=True,
                            description=f"[green]✓[/green] OneNote: {len(pages)} pages, "
                                        f"{sum(len(p.images) for p in pages)} images")

            drive_files = []
            if config.onedrive_folder:
                t = progress.add_task("Reading OneDrive activity files…")
                drive_files = _run(
                    "OneDrive",
                    lambda: collect_onedrive_files(graph, config.onedrive_folder),
                    [],
                    warnings,
                )
                progress.update(t, completed=True,
                                description=f"[green]✓[/green] OneDrive: {len(drive_files)} file(s) from '{config.onedrive_folder}'")

            if config.notes_folder:
                t = progress.add_task("Reading local notes folder…")
                local_files = _run(
                    "Notes folder",
                    lambda: collect_local_notes(config.notes_folder),
                    [],
                    warnings,
                )
                drive_files = drive_files + local_files
                progress.update(t, completed=True,
                                description=f"[green]✓[/green] Notes: {len(local_files)} file(s) from '{config.notes_folder}'")

    # Print collector warnings
    for label, msg in warnings:
        console.print(_friendly_warning(label, msg))

    # ── Manual fallback when all M365 sources are empty ───────────────────────
    manual_notes = ""
    all_empty = not any([events, emails, messages, pages, drive_files])
    if all_empty:
        console.print()
        console.print(Panel(
            "No data was retrieved from Microsoft 365.\n"
            "You can describe your work for the day below and the AI will generate entries from it.\n"
            "[dim]Example: 'Spent 3h on the payment module, 2h in sprint planning, 1h code review for PR #45'[/dim]",
            title="[yellow]Manual input[/yellow]",
            border_style="yellow",
        ))
        manual_notes = Prompt.ask(
            "Describe your work (or press [bold]Enter[/bold] to skip)",
            default="",
        ).strip()

    # ── Always ask for Teams / email supplement ───────────────────────────────
    console.print()
    teams_email_notes = Prompt.ask(
        "[cyan]Teams & email activity[/cyan] — describe any calls, chats or email threads "
        "(or press [bold]Enter[/bold] to skip)\n"
        "[dim]  e.g. '1h Teams client call with Apollo, 30min standup, 45min email replies to project queries'[/dim]",
        default="",
    ).strip()
    if teams_email_notes:
        manual_notes = (manual_notes + "\n" + teams_email_notes).strip()

    with FlexHRClient(config) as flexhr:
        ai_client = make_openrouter_client(config.openrouter_api_key)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            t = progress.add_task("Analysing with AI…")
            result = process_with_openrouter(
                ai_client, config.openrouter_model, target_date,
                events, emails, messages, pages,
                activity_types, projects,
                manual_notes=manual_notes,
                drive_files=drive_files,
            )
            entry_count = len(result.get("entries", []))
            progress.update(t, completed=True,
                            description=f"[green]✓[/green] AI: {entry_count} proposed entries")

    # Resolve project display names → Frappe document IDs
    for entry in result.get("entries", []):
        raw = entry.get("project", "")
        # AI may return "Apollo Project (PROJ-0034)" or "Apollo Project" or "PROJ-0034"
        # Strip the "(ID)" suffix if present, then look up
        m = re.search(r'\(([^)]+)\)\s*$', raw)
        if m:
            entry["project"] = m.group(1)
        elif raw.lower() in project_lookup:
            entry["project"] = project_lookup[raw.lower()]

    # ── Interactive review ────────────────────────────────────────────────────
    approved = preview_and_approve(
        entries=result.get("entries", []),
        notes=result.get("notes", ""),
        target_date=target_date.isoformat(),
    )

    if not approved:
        raise typer.Exit(0)

    # ── Submit ────────────────────────────────────────────────────────────────
    console.print("\nSubmitting to FlexHR…")
    try:
        with FlexHRClient(config) as flexhr:
            resp, was_updated = flexhr.submit_timesheet(target_date, approved)
        doc_name = resp.get("data", {}).get("name", "")
        if was_updated:
            console.print(f"[bold green]✓ Timesheet updated![/bold green]  Rows appended to [cyan]{doc_name}[/cyan]")
        else:
            console.print(f"[bold green]✓ Timesheet created![/bold green]  Document: [cyan]{doc_name}[/cyan]")
    except Exception as exc:
        console.print(f"[bold red]Submission failed:[/bold red] {exc}")
        if "PermissionError" in str(exc):
            console.print(
                "[dim]Hint: Ask your HR admin to grant your Frappe user the "
                "[bold]Employee Self Service[/bold] role.[/dim]"
            )
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
