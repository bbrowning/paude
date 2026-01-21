# Python Port Feature

This directory contains research and planning documentation for migrating paude from Bash to Python.

## Documents

| Document | Purpose |
|----------|---------|
| [RESEARCH.md](RESEARCH.md) | Technical research including codebase analysis, Python best practices, framework comparisons |
| [PLAN.md](PLAN.md) | Comprehensive 16-phase migration plan with tasks and acceptance criteria |

## Overview

The Python port will:

- **Maintain identical functionality** - All CLI flags, config detection, container behavior preserved
- **Use Python best practices** - Type hints, src layout, pyproject.toml, ruff + mypy
- **Improve maintainability** - Better structure, easier debugging, standard tooling
- **Enable standard distribution** - Install via pip/pipx from PyPI

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| CLI Framework | Typer | Type hints native, minimal boilerplate, built on Click |
| Project Layout | src/ | Standard for packages, prevents import issues |
| Container Interaction | subprocess | Exact parity with bash, no extra dependencies |
| Testing | pytest | Standard for Python, good CLI testing support |
| Linting | ruff + mypy | Fast, comprehensive, modern |
| Build Backend | hatchling | Modern, well-supported |

## Migration Phases

1. **Scaffolding** - Project structure, pyproject.toml, tooling
2. **CLI Skeleton** - Typer app with all flags
3. **Config Module** - Detection, parsing, Dockerfile generation
4. **Hash Module** - Identical hash computation
5. **Features Module** - Dev container feature support
6. **Mount Builder** - Volume mount configuration
7. **Container Management** - Build, pull, run containers
8. **Environment Setup** - Vertex AI environment
9. **Platform Handling** - macOS-specific logic
10. **Utilities** - Helper functions
11. **Dry-Run Mode** - Config preview
12. **Orchestration** - Main flow integration
13. **Test Migration** - All tests to pytest
14. **Documentation** - README, CONTRIBUTING updates
15. **CI/CD** - GitHub Actions updates
16. **Release** - v0.4.0 on PyPI

## Verification Checklist

Before release, verify:

- [ ] All 16 phases complete with acceptance criteria met
- [ ] All bash tests have passing Python equivalents
- [ ] Hash computation produces identical results to bash
- [ ] Dry-run output matches bash version
- [ ] macOS volume detection works
- [ ] Proxy container setup works
- [ ] Integration test with real Vertex AI passes
- [ ] `pipx install paude` works
- [ ] Documentation updated

## Next Steps

1. **Complete PREREQ-1 on host**: Update `paude.json` to add Python packages (see TASKS.md)
2. **Rebuild container**: Run `PAUDE_DEV=1 ./paude --rebuild` to pick up Python packages
3. **Run implementation loop**: Use the prompt in `prompts.md` to start the Ralph loop

## Document Overview

| Document | Purpose |
|----------|---------|
| [RESEARCH.md](RESEARCH.md) | Technical research and framework comparisons |
| [PLAN.md](PLAN.md) | High-level 16-phase migration plan |
| [TASKS.md](TASKS.md) | **Detailed task list for implementation** |
| [prompts.md](prompts.md) | Ralph loop prompts for each phase |
