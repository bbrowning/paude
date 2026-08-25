"""Shared test doubles for paude's container and transport layers."""

from __future__ import annotations

import io
import subprocess
from dataclasses import MISSING, fields
from typing import cast
from unittest.mock import MagicMock

from paude.backends.labels import SessionSpec
from paude.backends.podman.backend import PodmanBackend
from paude.backends.podman.proxy import PodmanProxyManager
from paude.backends.podman.resources import SessionResources
from paude.backends.podman.session_setup import SessionSetup
from paude.container.engine import ContainerEngine
from paude.container.network import NetworkManager
from paude.container.runner import ContainerRunner
from paude.container.volume import VolumeManager


class FakePopen:
    """Minimal stand-in for a ``subprocess.Popen[bytes]``.

    ``pending_waits`` is how many ``wait()`` calls raise ``TimeoutExpired``
    (simulating a still-running process) before the real return code is
    reported; ``kill()`` clears the count so a subsequent ``wait()`` succeeds.
    """

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        pending_waits: int = 0,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self._returncode = returncode
        self._pending_waits = pending_waits
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if self._pending_waits > 0:
            self._pending_waits -= 1
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        return self._returncode

    def poll(self) -> int | None:
        return None if self._pending_waits > 0 else self._returncode

    def kill(self) -> None:
        self.killed = True
        self._pending_waits = 0


def as_popen(proc: FakePopen) -> subprocess.Popen[bytes]:
    """Present a :class:`FakePopen` where a real ``Popen`` is required.

    FakePopen implements the subset ``subprocess_utils`` actually uses (wait,
    poll, kill, stdout, stderr). Casting here documents that, rather than
    widening a production signature to accommodate a test double.
    """
    return cast("subprocess.Popen[bytes]", proc)


class FakeTransport:
    """Transport that records commands instead of executing them.

    Satisfies :class:`paude.transport.base.Transport` so it can back a *real*
    :class:`~paude.container.engine.ContainerEngine`. That matters: every
    engine property tests care about (``is_podman``, ``is_remote``,
    ``default_bridge_network``, ``supports_multi_network_create``, ...) is
    derived from the binary name and the transport, so driving the real engine
    exercises the production derivations rather than a reimplementation of
    them that can silently disagree.

    ``results`` maps a substring of the joined command to the
    ``CompletedProcess`` to return for it; anything unmatched gets
    ``default_result``.
    """

    def __init__(
        self,
        *,
        is_remote: bool = False,
        host_label: str = "local",
        results: dict[str, subprocess.CompletedProcess[str]] | None = None,
        default_result: subprocess.CompletedProcess[str] | None = None,
    ) -> None:
        self._is_remote = is_remote
        self._host_label = host_label
        self._results = results or {}
        self._default = default_result or subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        self.commands: list[list[str]] = []
        self.copies: list[tuple[str, str, str]] = []

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        capture: bool = True,
        text: bool = True,
        input: str | None = None,  # noqa: A002
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(cmd))
        joined = " ".join(cmd)
        for needle, result in self._results.items():
            if needle in joined:
                return result
        return self._default

    def run_interactive(self, cmd: list[str]) -> int:
        self.commands.append(list(cmd))
        return 0

    def popen_binary(self, cmd: list[str]) -> subprocess.Popen[bytes]:
        raise NotImplementedError(
            "FakeTransport does not stream; use FakePopen directly instead."
        )

    def machine(self) -> str:
        return "x86_64"

    def copy_to_host(self, local_path: str, host_path: str) -> None:
        self.copies.append(("to", local_path, host_path))

    def copy_from_host(self, host_path: str, local_path: str) -> None:
        self.copies.append(("from", host_path, local_path))

    @property
    def is_remote(self) -> bool:
        return self._is_remote

    @property
    def host_label(self) -> str:
        return self._host_label


def make_engine(
    binary: str = "podman",
    *,
    transport: object | None = None,
    is_remote: bool = False,
) -> ContainerEngine:
    """Build a real ``ContainerEngine`` backed by a :class:`FakeTransport`."""
    return ContainerEngine(
        binary,
        transport=transport or FakeTransport(is_remote=is_remote),  # type: ignore[arg-type]
    )


def recorded_commands(engine: ContainerEngine) -> list[list[str]]:
    """Commands the :class:`FakeTransport` behind ``engine`` has recorded.

    ``ContainerEngine.transport`` is typed as the ``Transport`` protocol, which
    has no ``commands``, so this narrows it in one place instead of at every
    assertion.
    """
    transport = engine.transport
    assert isinstance(transport, FakeTransport), (
        f"engine is not backed by a FakeTransport: {transport!r}"
    )
    return transport.commands


def make_runner(engine: ContainerEngine | None = None, **attrs: object) -> MagicMock:
    """Build a ``ContainerRunner`` double with a real engine behind it.

    Specced against the real class, so a typo'd method name fails loudly
    instead of silently returning a truthy ``MagicMock``. Call assertions work
    exactly as they do on a bare ``MagicMock``.
    """
    runner = MagicMock(spec=ContainerRunner)
    runner.engine = engine or make_engine()
    for name, value in attrs.items():
        getattr(runner, name).return_value = value
    return runner


def make_backend(
    runner: MagicMock | None = None,
    network_manager: MagicMock | None = None,
    volume_manager: MagicMock | None = None,
    engine_binary: str = "podman",
) -> PodmanBackend:
    """Build a ``PodmanBackend`` with its collaborators replaced by doubles.

    Every collaborator is substituted, so no test reaches a real container
    engine. Callers may pass their own doubles to assert on interactions; the
    proxy manager, session setup and session resources are rebuilt afterwards
    so they observe the substituted runner rather than the discarded real one.
    """
    backend = PodmanBackend(engine=make_engine(engine_binary))
    if runner is None:
        runner = make_runner(make_engine(engine_binary))
    elif not isinstance(getattr(runner.engine, "binary", None), str):
        # A bare MagicMock() runner whose engine was never configured: give it
        # a real engine so binary-dependent branches behave like production. A
        # caller that configured its own engine double (to assert on
        # engine.run, say) keeps it.
        runner.engine = make_engine(engine_binary)
    backend._runner = runner
    backend._engine = runner.engine

    if network_manager is None:
        network_manager = MagicMock(spec=NetworkManager)
    if isinstance(network_manager.get_network_gateway.return_value, MagicMock):
        network_manager.get_network_gateway.return_value = "10.89.0.1"
    backend._network_manager = network_manager

    backend._volume_manager = volume_manager or MagicMock(spec=VolumeManager)
    backend._proxy = PodmanProxyManager(runner, network_manager)
    backend._setup = SessionSetup(runner, runner.engine)
    backend._resources = SessionResources(
        runner, network_manager, backend._volume_manager, backend._proxy
    )
    backend._port_forward = MagicMock()
    return backend


def assert_carries_every_spec_field(manifest: SessionSpec, built_by: str) -> None:
    """Assert every ``SessionSpec`` field survived the copy into ``manifest``.

    Both manifests inherit ``SessionSpec``, which dedupes the *schema* but not
    the *copying* -- each builder still assigns the fields explicitly so mypy
    can check them. A tenth label would therefore persist as its default until
    someone updated both builders. This fails by name instead, so long as the
    manifest was built from a spec whose fields are all non-default.
    """
    for spec_field in fields(SessionSpec):
        default = (
            spec_field.default_factory()
            if spec_field.default_factory is not MISSING
            else spec_field.default
        )
        assert getattr(manifest, spec_field.name) != default, (
            f"{built_by} does not carry SessionSpec.{spec_field.name}"
        )
