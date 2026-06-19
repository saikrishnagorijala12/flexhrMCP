from typing import Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

console = Console()

_CONF_COLOR = {"high": "green", "medium": "yellow", "low": "red"}


def _fmt_hours(h: float) -> str:
    ih = int(h)
    m = int(round((h - ih) * 60))
    return f"{ih}h {m}m" if m else f"{ih}h"


def _render_table(entries: list[dict], target_date: str) -> None:
    table = Table(
        title=f"Proposed Timesheet — {target_date}",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_header=True,
    )
    table.add_column("#",             style="dim",    width=3)
    table.add_column("Activity Type",                 width=16)
    table.add_column("Hrs",           justify="right", width=6)
    table.add_column("Project",       style="bold")
    table.add_column("Description")
    table.add_column("Conf.",                         width=6)

    for i, e in enumerate(entries, 1):
        conf = e.get("confidence", "medium")
        desc = e.get("description", "")
        if len(desc) > 70:
            desc = desc[:67] + "…"
        table.add_row(
            str(i),
            e["activity_type"],
            _fmt_hours(e["hours"]),
            e["project"],
            desc,
            Text(conf, style=_CONF_COLOR.get(conf, "white")),
        )

    total = sum(e["hours"] for e in entries)
    console.print()
    console.print(table)
    console.print(f"  [bold]Total: {_fmt_hours(total)}[/bold]\n")


def preview_and_approve(
    entries: list[dict],
    notes: str = "",
    target_date: str = "",
) -> Optional[list[dict]]:
    """Interactive terminal preview.

    Returns the (possibly edited) approved entries, or None if the user quits.
    """
    if not entries:
        console.print(Panel(
            "[yellow]No time entries were found for this date.[/yellow]\n"
            "Try running with [bold]--date[/bold] for a different day, or check that your "
            "Microsoft 365 sources contain activity for this date.",
            title="No entries",
        ))
        return None

    if notes:
        console.print(Panel(f"[dim]{notes}[/dim]", title="AI Notes", border_style="dim"))

    while True:
        _render_table(entries, target_date)
        console.print(
            "[bold]Commands:[/bold]  "
            "[green]s[/green] submit  "
            "[yellow]e[/yellow][dim]<n>[/dim] edit  "
            "[red]d[/red][dim]<n>[/dim] delete  "
            "[dim]q[/dim] quit"
        )
        action = Prompt.ask("→", default="s").strip().lower()

        if action in ("s", "submit"):
            if Confirm.ask(f"Submit {len(entries)} entr{'y' if len(entries)==1 else 'ies'} to FlexHR?"):
                return entries

        elif action in ("q", "quit"):
            console.print("[dim]Cancelled — nothing was submitted.[/dim]")
            return None

        elif action.startswith("d"):
            try:
                idx = int(action[1:].strip()) - 1
                if 0 <= idx < len(entries):
                    removed = entries.pop(idx)
                    console.print(f"[red]Deleted:[/red] {removed['description'][:60]}")
                else:
                    console.print("[red]Entry number out of range.[/red]")
            except (ValueError, IndexError):
                console.print("[red]Usage: d<n>  e.g. d2[/red]")

        elif action.startswith("e"):
            try:
                idx = int(action[1:].strip()) - 1
                if 0 <= idx < len(entries):
                    e = entries[idx]
                    console.print(f"\n[bold]Editing entry #{idx + 1}[/bold]")
                    e["hours"]         = float(Prompt.ask("Hours",         default=str(e["hours"])))
                    e["project"]       = Prompt.ask("Project",       default=e["project"])
                    e["activity_type"] = Prompt.ask("Activity type", default=e["activity_type"])
                    e["description"]   = Prompt.ask("Description",   default=e["description"])
                    e["confidence"]    = "high"  # user-edited entries are authoritative
                else:
                    console.print("[red]Entry number out of range.[/red]")
            except (ValueError, IndexError):
                console.print("[red]Usage: e<n>  e.g. e1[/red]")

        else:
            console.print("[dim]Unknown command. Use s / e<n> / d<n> / q[/dim]")
