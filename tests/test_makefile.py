"""Tests for Make command boundaries."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("target", "expected_args"),
    [
        pytest.param(
            "test",
            "run pytest --cov=paude --cov-report=term-missing",
            id="unit",
        ),
        pytest.param(
            "test-all",
            "run pytest -o addopts=-v --cov=paude --cov-report=term-missing",
            id="all",
        ),
        pytest.param(
            "test-integration",
            "run pytest tests/integration/ -v -m integration",
            id="integration",
        ),
        pytest.param(
            "test-podman",
            "run pytest tests/integration/ -v -m podman",
            id="podman",
        ),
    ],
)
def test_pytest_target_enforces_color_environment(
    tmp_path: Path,
    target: str,
    expected_args: str,
) -> None:
    """Every pytest target gives its child a deterministic color environment."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invocation_log = tmp_path / "uv-invocation"
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "${NO_COLOR+x}" "${FORCE_COLOR-}" '
        '"${TERM-}" "$*" >"$UV_INVOCATION_LOG"\n'
    )
    uv.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "NO_COLOR": "1",
            "FORCE_COLOR": "0",
            "TERM": "dumb",
            "UV_INVOCATION_LOG": str(invocation_log),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["make", target],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert invocation_log.read_text().splitlines() == [
        "",
        "1",
        "xterm-256color",
        expected_args,
    ]
