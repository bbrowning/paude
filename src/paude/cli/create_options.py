"""Shared ``Annotated`` option types for ``paude create``."""

from __future__ import annotations

from typing import Annotated

import typer

AllowedDomainsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--allowed-domains",
        help=(
            "Domains to allow network access. Can be repeated. "
            "Special values: 'all' (unrestricted), "
            "'default' (vertexai+python+github), "
            "'vertexai', 'python', 'golang', 'nodejs', "
            "'rust'. Default: 'default'."
        ),
    ),
]

AllowedEndpointsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--allowed-endpoints",
        help=(
            "Exact host:port exceptions for nonstandard proxy ports. "
            "Can be repeated; each host must also be allowed by domains."
        ),
    ),
]

EnvOption = Annotated[
    list[str] | None,
    typer.Option(
        "--env",
        "-e",
        help=(
            "Set an env var in the agent container as KEY=VALUE, or KEY "
            "to copy the host value. Can be repeated. Not for secrets."
        ),
    ),
]

CredentialDomainOption = Annotated[
    list[str] | None,
    typer.Option(
        "--credential-domain",
        help=(
            "Have the proxy also inject a provider's API key for a custom "
            "host, as PROVIDER=HOST[:PORT] (e.g. anthropic=gw.example.com). "
            "The host is added to the allowlist. Can be repeated."
        ),
    ),
]

AgentOption = Annotated[
    str | None,
    typer.Option(
        "--agent",
        help=(
            "Agent to use: claude (default), codex, cursor, gascity, "
            "gemini, openclaw, opencode. Alias for a single-item --agents."
        ),
    ),
]

AgentsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--agents",
        help=(
            "Agents to use (comma-separated and/or repeatable; first is "
            "primary), e.g. --agents gascity,claude,codex. Cannot be "
            "combined with --agent."
        ),
    ),
]

ProviderOption = Annotated[
    str | None,
    typer.Option(
        "--provider",
        help=(
            "Provider mapping for the primary agent (e.g., vertex, openai). "
            "Cannot be combined with --agent-provider."
        ),
    ),
]

ProvidersOption = Annotated[
    list[str] | None,
    typer.Option(
        "--providers",
        help=(
            "Credential providers to configure in the proxy and container "
            "(comma-separated and/or repeatable)."
        ),
    ),
]

AgentProviderOption = Annotated[
    list[str] | None,
    typer.Option(
        "--agent-provider",
        help=(
            "Map an installed agent to a provider as AGENT=PROVIDER. "
            "Comma-separated and/or repeatable. Cannot be combined with "
            "--provider."
        ),
    ),
]

GpuOption = Annotated[
    str | None,
    typer.Option(
        "--gpu",
        help=(
            "Pass GPU devices to the container. "
            "Use --gpu without a value for all GPUs, "
            "or --gpu=device=0,1 for specific devices."
        ),
    ),
]
