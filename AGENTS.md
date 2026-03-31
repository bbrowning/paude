# Paude

Container wrapper that runs AI coding agents (Claude Code, Cursor, Gemini CLI) in isolated, secure containers.

## Build and Test

```bash
make test              # Unit tests (pytest)
make test-all          # Unit + integration tests
make lint              # Ruff linter
make typecheck         # Mypy strict mode
make format            # Auto-format with ruff
make build             # Build container images locally
make clean && make run # Rebuild after container changes
```

## Development Rules

- **All new features must include tests.** Add tests in `tests/test_<module>.py`; CLI flag tests go in `tests/test_cli.py`.
- **Before committing**, run `make lint` and `make typecheck`. Do not commit if either fails.
- When adding or changing user-facing features, update `README.md` and `src/paude/cli/help.py`.
- Use agent-agnostic language in user-facing text. Say "the agent" not "Claude" (except in agent-specific examples).
- When adding scripts to `containers/paude/`, also add them to `[tool.hatch.build.targets.wheel.force-include]` in `pyproject.toml` so they get bundled into the installed package.
- When discovering unrelated bugs or tech debt, add them to `KNOWN_ISSUES.md` and continue with the current task.

## Project Layout

- `src/paude/` -- Python package. Key areas: `agents/` (agent definitions), `backends/` (podman and openshift), `cli/` (typer CLI), `config/` (configuration), `container/` (container management), `transport/` (local/SSH execution).
- `containers/paude/` -- Main container Dockerfile and entrypoints.
- `containers/proxy/` -- Network filtering proxy container.
- `tests/` -- Pytest test suite.

## Further Reading

- [Coding Standards](docs/CODING_STANDARDS.md) -- file/method size limits, refactoring triggers, shared utility locations
- [Security Model](docs/SECURITY.md) -- mount restrictions, credential handling, attack vectors
- [Configuration](docs/CONFIGURATION.md) -- config file format and resolution
- [Remote Sessions](docs/REMOTE.md) -- SSH-based remote execution
- [OpenShift Backend](docs/OPENSHIFT.md) -- Kubernetes deployment
- [Orchestration](docs/ORCHESTRATION.md) -- multi-agent workflows
- [Sessions](docs/SESSIONS.md) -- session lifecycle
