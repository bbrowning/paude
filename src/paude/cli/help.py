"""Custom help group for paude CLI with extra reference sections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import click
import typer.core
from rich.console import Console
from rich.console import Group as RichGroup
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


@dataclass(frozen=True, slots=True)
class HelpSection:
    """A titled help section rendered as a Rich panel.

    Sections can contain two-column ``rows`` (command/description pairs),
    free-form ``text``, or both.  When both are present the table is
    rendered first, followed by the text.
    """

    title: str
    rows: tuple[tuple[str, str], ...] = ()
    text: str = ""


_SECTIONS: tuple[HelpSection, ...] = (
    HelpSection(
        title="Workflow",
        rows=(
            ("Quick start:", ""),
            (
                "paude create my-project --git",
                "Create, start, push code+tags, set origin",
            ),
            ("paude connect my-project", "Connect to running session"),
            ("", ""),
            ("Manual workflow:", ""),
            ("paude create my-project", "Create and start session"),
            ("paude remote add --push my-project", "Init git repo + push code"),
            ("paude connect my-project", "Connect to running session"),
            ("", ""),
            ("Later:", ""),
            ("paude connect", "Reconnect to running session"),
            ("git push paude-<name> main", "Push more changes to container"),
            ("paude stop", "Stop session (preserves data)"),
            ("paude delete NAME --confirm", "Delete session permanently"),
        ),
    ),
    HelpSection(
        title="Syncing Code (via git)",
        rows=(
            ("paude remote add [NAME]", "Add git remote (requires running container)"),
            ("paude remote add --push [NAME]", "Add remote AND push current branch"),
            ("paude remote list", "List all paude git remotes"),
            ("paude remote remove [NAME]", "Remove git remote for session"),
            ("paude remote cleanup", "Remove remotes for deleted sessions"),
            ("git push paude-<name> main", "Push code to container"),
            ("git pull paude-<name> main", "Pull changes from container"),
        ),
    ),
    HelpSection(
        title="Copying Files (without git)",
        rows=(
            ("paude cp ./file.txt session:file.txt", "Copy local file to session"),
            ("paude cp session:output.log ./", "Copy file from session to local"),
            ("paude cp ./src :src", "Auto-detect session, copy dir"),
            ("paude cp :results ./results", "Auto-detect session, copy from"),
        ),
    ),
    HelpSection(
        title="Egress Filtering",
        rows=(
            ("paude allowed-domains NAME", "Show current domains"),
            ("paude allowed-domains NAME --add .example.com", "Add domain to list"),
            ("paude allowed-domains NAME --remove .pypi.org", "Remove domain"),
            (
                "paude allowed-domains NAME --replace default .example.com",
                "Replace entire list",
            ),
            ("paude blocked-domains NAME", "Show blocked domains"),
            ("paude blocked-domains NAME --raw", "Show raw proxy log"),
        ),
    ),
    HelpSection(
        title="Examples",
        rows=(
            (
                "paude create --yolo --allowed-domains all",
                "Create session with full autonomy (DANGEROUS)",
            ),
            (
                "paude create --allowed-domains default --allowed-domains .example.com",
                "Add custom domain to defaults",
            ),
            (
                "paude create --allowed-domains .example.com",
                "Allow ONLY custom domain (replaces defaults)",
            ),
            ("paude create -a '-p \"prompt\"'", "Create session with initial prompt"),
            ("paude create --dry-run", "Verify configuration without creating"),
            ("paude create --backend=docker", "Create session using Docker engine"),
            ("paude create --backend=openshift", "Create session on OpenShift cluster"),
            (
                "paude create --backend=docker --host user@gpu-box",
                "Run container on remote host via SSH",
            ),
            (
                "paude create --host user@host --ssh-key ~/.ssh/id_ed25519",
                "Remote host with specific SSH key",
            ),
            (
                "paude create --gpu all --host user@gpu-box",
                "Pass all GPUs on remote host",
            ),
        ),
    ),
    HelpSection(
        title="Configuration",
        rows=(
            ("paude config show", "Show resolved defaults for current directory"),
            ("paude config path", "Print user config file path"),
            ("paude config init", "Create starter ~/.config/paude/defaults.json"),
        ),
        text=(
            "Settings resolved: CLI flags > paude.json"
            " > user defaults.\n"
            "User defaults: ~/.config/paude/defaults.json"
            " (backend, yolo, git, domains, etc.)\n"
            'Project hints: paude.json "create" section'
            " (allowed-domains, agent)"
        ),
    ),
    HelpSection(
        title="Security",
        text=(
            "By default, paude runs with network restricted"
            " to Vertex AI, PyPI, and GitHub.\n"
            "Use --allowed-domains all to permit all"
            " network access (enables data exfil).\n"
            "Combining --yolo with --allowed-domains all"
            " is maximum risk mode.\n"
            "PAUDE_GITHUB_TOKEN is explicit only;"
            " host GH_TOKEN is never auto-propagated."
        ),
    ),
    HelpSection(
        title="Agents",
        rows=(
            ("--agent claude", "Claude Code (default)"),
            ("--agent cursor", "Cursor CLI"),
            ("--agent gemini", "Gemini CLI"),
        ),
    ),
)

# Matches typer.rich_utils.STYLE_COMMANDS_TABLE_FIRST_COLUMN
_FIRST_COL_STYLE = "bold cyan"
# Matches typer.rich_utils.STYLE_OPTIONS_PANEL_BORDER
_PANEL_BORDER_STYLE = "dim"


def _build_table(rows: tuple[tuple[str, str], ...]) -> Table:
    """Build a two-column table matching Typer's command-table style."""
    table = Table(
        show_header=False,
        show_edge=False,
        box=None,
        pad_edge=False,
    )
    table.add_column(style=_FIRST_COL_STYLE, no_wrap=True)
    table.add_column()
    for left, right in rows:
        table.add_row(left, right)
    return table


def _build_panel(section: HelpSection) -> Panel:
    """Build a Rich Panel for a help section."""
    parts: list[Table | Text] = []
    if section.rows:
        parts.append(_build_table(section.rows))
    if section.text:
        if parts:
            parts.append(Text(""))  # blank line between table and text
        parts.append(Text(section.text))
    content = parts[0] if len(parts) == 1 else RichGroup(*parts)
    return Panel(
        content,
        border_style=_PANEL_BORDER_STYLE,
        title=section.title,
        title_align="left",
    )


class PaudeGroup(typer.core.TyperGroup):
    """Custom Click group that appends extra help sections as Rich panels."""

    help_sections: ClassVar[tuple[HelpSection, ...]] = _SECTIONS

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Render native Typer help, then append extra reference sections."""
        super().format_help(ctx, formatter)
        console = Console(highlight=False)
        for section in self.help_sections:
            console.print(_build_panel(section))
