"""User-supplied ``--env`` variables for the agent container."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_RESERVED_PREFIX = "PAUDE_"


def _reserved_names() -> set[str]:
    """Variables paude sets on the agent container itself.

    Letting a user override them would break proxy routing, CA trust, or git
    configuration.
    """
    from paude.environment import build_proxy_environment

    reserved = {name.upper() for name in build_proxy_environment("proxy")}
    reserved.add("GIT_CONFIG_GLOBAL")
    return reserved


def _check_name(name: str, secrets: set[str], reserved: set[str]) -> None:
    if not _ENV_NAME.match(name):
        raise ValueError(f"Invalid --env name '{name}'.")
    if name in secrets:
        hint = (
            "Export PAUDE_GITHUB_TOKEN on the host instead."
            if name == "GH_TOKEN"
            else "Export it on the host and use --provider (plus "
            "--credential-domain to send it to a custom host)."
        )
        raise ValueError(
            f"--env cannot set credential '{name}'; it is held by the proxy "
            f"sidecar. {hint}"
        )
    if name.upper() in reserved or name.upper().startswith(_RESERVED_PREFIX):
        raise ValueError(f"--env cannot set '{name}'; paude manages it.")


def parse_env_options(
    values: list[str] | None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Parse ``--env KEY=VALUE`` / ``--env KEY`` into ``KEY=VALUE`` entries.

    A bare ``KEY`` copies the value from the host environment. Later
    occurrences of a key replace earlier ones.

    Raises:
        ValueError: On an invalid or reserved name, or a bare ``KEY`` that is
            unset on the host.
    """
    if not values:
        return []
    from paude.backends.proxy_config import all_proxy_credential_env_vars

    host_env = os.environ if environ is None else environ
    secrets = all_proxy_credential_env_vars()
    reserved = _reserved_names()
    parsed: dict[str, str] = {}
    for value in values:
        name, sep, val = value.partition("=")
        _check_name(name, secrets, reserved)
        if not sep:
            if name not in host_env:
                raise ValueError(
                    f"--env {name} copies the host value, but {name} is not set."
                )
            val = host_env[name]
        parsed.pop(name, None)
        parsed[name] = val
    return [f"{name}={val}" for name, val in parsed.items()]


def env_entries_to_dict(entries: list[str]) -> dict[str, str]:
    """Turn stored ``KEY=VALUE`` entries back into a mapping."""
    return {key: val for key, _, val in (e.partition("=") for e in entries)}
