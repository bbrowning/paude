"""Help text display for paude CLI."""

from __future__ import annotations

import typer


def show_help() -> None:
    """Show custom help message matching bash format."""
    help_text = """paude - Run AI coding agents in secure containers

USAGE:
    paude                           List all sessions
    paude <COMMAND> [OPTIONS]

COMMANDS:
    create [NAME]       Create a new persistent session
    start [NAME]        Start a session and connect to it
    stop [NAME]         Stop a session (preserves data)
    connect [NAME]      Attach to a running session
    list                List all sessions
    cp SRC DEST         Copy files between local and session
    remote <ACTION>     Manage git remotes for code sync
    allowed-domains NAME
                        Manage allowed egress domains for a session
    blocked-domains NAME
                        Show domains blocked by the proxy for a session
    delete NAME         Delete a session and all its resources

OPTIONS (for 'create' command):
    --agent             Agent to use: claude (default), gemini
    --yolo              Enable YOLO mode (skip all permission prompts)
    --allowed-domains   Domains to allow network access (repeatable).
                        Special: 'all', 'default' (vertexai+python+github)
                        Aliases: 'vertexai', 'python', 'golang', 'nodejs',
                                 'rust', 'github'
                        Custom domains REPLACE defaults; use with 'default' to add.
    --git               Set up git remote, push code+tags, configure origin
    --rebuild           Force rebuild of workspace container image
    --dry-run           Show configuration without creating session
    -a, --args          Arguments to pass to the agent (e.g., -a '-p "prompt"')
    --backend           Container backend: podman (default), openshift
    --platform          Target platform for image builds (e.g., linux/amd64)
    --openshift-context Kubeconfig context for OpenShift
    --openshift-namespace
                        OpenShift namespace (default: current context)

OPTIONS (for 'start' and 'connect' commands):
    --github-token      GitHub personal access token for gh CLI.
                        Use a fine-grained read-only PAT.
                        Also reads PAUDE_GITHUB_TOKEN env var (flag takes priority).
                        Token is injected at connect time only, never stored.

OPTIONS (global):
    -h, --help          Show this help message and exit
    -V, --version       Show paude version and exit

WORKFLOW:
    # Quick start (create + push code in one step):
    paude create my-project --git   # Create, start, push code+tags, set origin
    paude connect my-project        # Connect to running session

    # Manual workflow:
    paude create my-project         # Create and start session
    paude remote add --push my-project  # Init git repo + push code
    paude connect my-project        # Connect to running session

    # Later:
    paude connect                   # Reconnect to running session
    git push paude-<name> main      # Push more changes to container
    paude stop                      # Stop session (preserves data)
    paude delete NAME --confirm     # Delete session permanently

SYNCING CODE (via git):
    paude remote add [NAME]         Add git remote (requires running container)
    paude remote add --push [NAME]  Add remote AND push current branch
    paude remote list               List all paude git remotes
    paude remote remove [NAME]      Remove git remote for session
    paude remote cleanup            Remove remotes for deleted sessions
    git push paude-<name> main      Push code to container
    git pull paude-<name> main      Pull changes from container

COPYING FILES (without git):
    paude cp ./file.txt my-session:file.txt     Copy local file to session
    paude cp my-session:output.log ./           Copy file from session to local
    paude cp ./src :src                         Auto-detect session, copy dir
    paude cp :results ./results                 Auto-detect session, copy from

EGRESS FILTERING:
    paude allowed-domains my-session                      Show current domains
    paude allowed-domains my-session --add .example.com   Add domain to list
    paude allowed-domains my-session --remove .pypi.org   Remove domain
    paude allowed-domains my-session --replace default .example.com
                                                          Replace entire list
    paude blocked-domains my-session                      Show blocked domains
    paude blocked-domains my-session --raw                Show raw proxy log

EXAMPLES:
    paude create --yolo --allowed-domains all
                                    Create session with full autonomy (DANGEROUS)
    paude create --allowed-domains default --allowed-domains .example.com
                                    Add custom domain to defaults
    paude create --allowed-domains .example.com
                                    Allow ONLY custom domain (replaces defaults)
    paude create -a '-p "prompt"'   Create session with initial prompt
    paude create --dry-run          Verify configuration without creating
    paude create --backend=openshift
                                    Create session on OpenShift cluster

SECURITY:
    By default, paude runs with network restricted to Vertex AI, PyPI, and GitHub.
    Use --allowed-domains all to permit all network access (enables data exfil).
    Combining --yolo with --allowed-domains all is maximum risk mode.
    PAUDE_GITHUB_TOKEN is explicit only; host GH_TOKEN is never auto-propagated.

AGENTS:
    --agent claude      Claude Code (default)
    --agent gemini      Gemini CLI"""
    typer.echo(help_text)


def help_callback(value: bool) -> None:
    """Print help and exit."""
    if value:
        show_help()
        raise typer.Exit()
