# Coding Standards

Project-specific size limits and conventions for the Paude codebase.

## Size Limits

| Metric | Evaluate | Hard Limit | Action |
|--------|----------|------------|--------|
| File length | > 300 lines | > 400 lines | Split into package with `__init__.py` preserving public API |
| Method/function | > 30 lines | > 50 lines | Extract helper methods |
| Methods per class | -- | > 20 | Decompose into collaborating classes |
| Duplicated code | -- | 2+ occurrences | Extract to shared utility |

Line counts exclude tests and docstrings.

## When to Split a File

- Multiple unrelated classes in one file
- File handles multiple layers of abstraction
- Class has internal helpers that could stand alone

When splitting, create a package directory with `__init__.py` that preserves the original module's public API.

## Shared Utility Locations

- Cross-cutting constants: `src/paude/constants.py`
- Shared backend logic: `src/paude/backends/shared.py`

## Testability

- Wrap external commands (`podman`, `oc`) in testable classes rather than calling subprocess directly.
