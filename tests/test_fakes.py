"""Tests for the shared test doubles in tests/fakes.py.

These guard the doubles themselves: a fake that drifts from the real class it
stands in for silently weakens every test that relies on it.
"""

from __future__ import annotations

import subprocess

import pytest

from paude.backends.podman.backend import PodmanBackend
from paude.container.engine import ContainerEngine
from tests.fakes import FakeTransport, make_backend, make_engine, make_runner


class TestFakeTransport:
    """Tests for FakeTransport."""

    def test_records_commands_without_executing(self) -> None:
        """run() records the command and returns the default result."""
        transport = FakeTransport()
        result = transport.run(["podman", "ps"])

        assert transport.commands == [["podman", "ps"]]
        assert result.returncode == 0

    def test_matches_scripted_result_by_substring(self) -> None:
        """A command containing a configured needle gets that result."""
        scripted = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="boom", stderr="err"
        )
        transport = FakeTransport(results={"network inspect": scripted})

        assert transport.run(["podman", "network", "inspect", "n"]).stdout == "boom"
        assert transport.run(["podman", "ps"]).returncode == 0

    def test_streaming_is_refused_rather_than_faked(self) -> None:
        """popen_binary raises instead of returning a half-working double."""
        with pytest.raises(NotImplementedError):
            FakeTransport().popen_binary(["podman", "ps"])


class TestMakeEngine:
    """Tests for make_engine."""

    def test_engine_derives_real_properties(self) -> None:
        """Properties come from the real ContainerEngine, not a reimplementation."""
        podman = make_engine("podman")
        docker = make_engine("docker")

        assert isinstance(podman, ContainerEngine)
        assert podman.is_podman
        assert not docker.is_podman
        assert podman.default_bridge_network == "podman"
        assert docker.default_bridge_network == "bridge"
        assert podman.supports_multi_network_create
        assert not docker.supports_multi_network_create

    def test_is_remote_follows_the_transport(self) -> None:
        """The engine reads is_remote off the transport it was given."""
        assert not make_engine().is_remote
        assert make_engine(transport=FakeTransport(is_remote=True)).is_remote

    def test_engine_run_reaches_the_transport(self) -> None:
        """engine.run prepends the binary and routes through the transport."""
        engine = make_engine("docker")
        engine.run("ps", "-a")

        assert engine.transport.commands == [["docker", "ps", "-a"]]


class TestMakeRunner:
    """Tests for make_runner."""

    def test_specced_against_the_real_runner(self) -> None:
        """A method that does not exist on ContainerRunner raises."""
        runner = make_runner()

        runner.container_exists("x")
        with pytest.raises(AttributeError):
            runner.container_exsits("x")  # noqa: B018 - deliberate typo

    def test_return_values_can_be_set_by_keyword(self) -> None:
        """Keyword arguments configure method return values."""
        runner = make_runner(container_exists=True, container_running=False)

        assert runner.container_exists("x") is True
        assert runner.container_running("x") is False


class TestMakeBackend:
    """Tests for make_backend."""

    def test_no_collaborator_reaches_a_real_engine(self) -> None:
        """Runner, proxy and setup all observe the substituted doubles."""
        backend = make_backend()

        assert isinstance(backend, PodmanBackend)
        assert backend._proxy._runner is backend._runner
        assert backend._setup._runner is backend._runner
        assert backend.engine is backend._runner.engine
        assert isinstance(backend.engine.transport, FakeTransport)

    def test_engine_binary_reaches_the_backend(self) -> None:
        """backend_type reflects the requested engine binary."""
        assert make_backend(engine_binary="docker").backend_type == "docker"

    def test_caller_supplied_engine_double_is_preserved(self) -> None:
        """A runner whose engine the caller configured is left alone."""
        runner = make_runner()
        configured = runner.engine
        backend = make_backend(runner)

        assert backend.engine is configured

    def test_gateway_default_is_only_a_default(self) -> None:
        """An explicitly configured gateway is not overwritten."""
        from unittest.mock import MagicMock

        network = MagicMock()
        network.get_network_gateway.return_value = "10.1.2.1"

        assert make_backend(network_manager=network)._network_manager is network
        assert network.get_network_gateway.return_value == "10.1.2.1"
