"""Tests for continuous-integration workflow guarantees."""

from pathlib import Path


def test_podman_integration_requires_supported_engine() -> None:
    """Podman integration runs on a supported image and checks its version."""
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    ).read_text()
    job = workflow.split("  podman-integration:\n", maxsplit=1)[1]

    assert "    runs-on: ubuntu-24.04\n" in job
    assert "podman version --format" in job
    assert "podman_major < 4" in job
