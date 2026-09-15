"""Rich-based terminal UI for Recoup.

No web framework. Works on Windows 8GB. Run with:
    PYTHONPATH=. python -m src.dashboard
"""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box
from collections import Counter

from fixtures.generate_dataset import generate
from src.orchestrator import run_pipeline
from src.diagnosis import StubProvider
from src.event_store import EventStore


def _render():
    console = Console()

    with console.status("[cyan]Generating 3,000-transaction dataset (seed=42)...[/cyan]", spinner="dots"):
        txns, ground_truth = generate(total_transactions=3000, seed=42)

    with console.status("[cyan]Running pipeline: rule → probabilistic → classify → diagnose → recover → audit...[/cyan]", spinner="dots"):
        result = run_pipeline(txns, llm_provider=StubProvider())

        # Record audit events for chain integrity check
        with EventStore(":memory:") as store:
            for d in result.rule_decisions:
                if d.matched:
                    store.append("rule_match", {"a": d.txn_id_a, "b": d.txn_id_b}, d.txn_id_a)
            for exc in result.exceptions:
                store.append("exception_classified", {"reason": exc.reason.value}, exc.txn_id)
            chain_ok, broken = store.verify_chain()
            chain_hash = store._last_hash()[:16] + "..."
            event_count = len(store.get_all_events())

    # Build layout
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=7),
        Layout(name="body"),
        Layout(name="floor", size=5),
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(name="sources"),
        Layout(name="rules"),
    )

    # Header — main numbers
    grid = Table.grid(expand=True)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="center", ratio=1)
    grid.add_column(justify="center", ratio=1)

    auto_matched = result.report.rule_matched + result.report.prob_matched
    grid.add_row(
        "[bold cyan]RECOUP[/bold cyan]\n[dim]Multi-source reconciliation\n+ control plane[/dim]",
        f"[bold green]{result.report.total}[/bold green]\ntxns ingested",
        f"[bold yellow]{auto_matched}[/bold yellow]\nauto-matched\n[dim]{result.report.auto_match_rate:.1f}%[/dim]",
        f"[bold red]{result.report.exceptions}[/bold red]\nexceptions\n[dim]{result.report.exception_rate:.1f}%[/dim]",
        f"[bold magenta]{event_count}[/bold magenta]\naudit events\n[dim]{'OK' if chain_ok else 'BROKEN'}[/dim]",
    )
    layout["header"].update(Panel(grid, border_style="cyan", box=box.HEAVY, title="[bold]END-TO-END PIPELINE"))

    # Sources table
    counts = Counter(t.source.value for t in txns)
    src_table = Table(title="Source Distribution", box=box.ROUNDED, expand=True)
    src_table.add_column("Source", style="cyan")
    src_table.add_column("Count", style="green", justify="right")
    src_table.add_column("Bar")
    max_n = max(counts.values()) if counts else 1
    for src in sorted(counts.keys()):
        n = counts[src]
        bar = "█" * int((n / max_n) * 25)
        src_table.add_row(src, str(n), f"[cyan]{bar}[/cyan]")
    layout["sources"].update(src_table)

    # Rules fired
    rule_counts = Counter()
    for d in result.rule_decisions:
        if d.matched:
            for r in d.reasons:
                rule_counts[r] += 1
    rule_table = Table(title="Rule Engine — Firings", box=box.ROUNDED, expand=True)
    rule_table.add_column("Rule", style="yellow")
    rule_table.add_column("Firings", style="green", justify="right")
    if rule_counts:
        for r, n in sorted(rule_counts.items(), key=lambda x: -x[1]):
            rule_table.add_row(r, str(n))
    else:
        rule_table.add_row("[dim](none)[/dim]", "0")
    layout["rules"].update(rule_table)

    # Exceptions panel
    text = Text()
    text.append(f"  {result.report.exceptions} transactions require investigation\n\n", style="bold red")
    by_reason = result.report.exception_breakdown
    for reason, n in sorted(by_reason.items(), key=lambda x: -x[1]):
        text.append(f"  • {reason:25s}  ", style="white")
        text.append(f"{n:>4}\n", style="bold red")
    text.append(f"\n  [dim]Chain hash: {chain_hash}[/dim]", style="dim")
    text.append(f"\n  [dim]LLM used: stub (no API in demo); refuses safely.[/dim]", style="dim")
    layout["right"].update(Panel(text, title="[bold red]⚠ Exception Queue[/bold red]", border_style="red", box=box.HEAVY))

    # Footer
    floor_grid = Table.grid(expand=True)
    floor_grid.add_column(justify="center", ratio=1)
    floor_grid.add_column(justify="center", ratio=1)
    floor_grid.add_column(justify="center", ratio=1)
    floor_grid.add_row(
        f"[green]LLM never touches money[/green]",
        f"[green]Idempotency on every action[/green]",
        f"[green]Tamper-evident audit chain[/green]",
    )
    layout["floor"].update(Panel(floor_grid, border_style="green", box=box.HEAVY))

    console.print(layout)


if __name__ == "__main__":
    _render()