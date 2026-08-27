"""Tests for durable allowed-domain proxy state."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from paude.backends.podman.proxy_state import ProxyStateError, ProxyStateStore
from paude.container.runner import ContainerRunner
from tests.fakes import FakeTransport, make_engine


def _result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestProxyStateStore:
    """The auth-volume record distinguishes absence, data, and failure."""

    def test_absent_legacy_record_is_none(self) -> None:
        runner = ContainerRunner(
            make_engine(transport=FakeTransport(default_result=_result(returncode=3)))
        )

        assert ProxyStateStore(runner).read("auth", "proxy:latest") is None

    def test_reads_empty_list_as_committed_data(self) -> None:
        payload = json.dumps({"schema": "allowed-domains.v1", "domains": []})
        runner = ContainerRunner(
            make_engine(transport=FakeTransport(default_result=_result(stdout=payload)))
        )

        assert ProxyStateStore(runner).read("auth", "proxy:latest") == []

    @pytest.mark.parametrize(
        "result",
        [
            _result(returncode=1, stderr="helper failed"),
            _result(stdout="not-json"),
            _result(stdout='{"schema":"wrong","domains":[]}'),
        ],
    )
    def test_read_failure_never_falls_back_to_stale_labels(
        self, result: subprocess.CompletedProcess[str]
    ) -> None:
        runner = ContainerRunner(
            make_engine(transport=FakeTransport(default_result=result))
        )

        with pytest.raises(ProxyStateError):
            ProxyStateStore(runner).read("auth", "proxy:latest")

    def test_write_uses_atomic_versioned_payload(self) -> None:
        runner = MagicMock(spec=ContainerRunner)
        runner.engine.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        ProxyStateStore(runner).write("auth", "proxy:latest", [".new.example"])

        call = runner.engine.run.call_args
        assert any("mv -f" in arg for arg in call.args)
        assert json.loads(call.kwargs["input"]) == {
            "schema": "allowed-domains.v1",
            "domains": [".new.example"],
        }
        assert "auth:/data/auth" in call.args
