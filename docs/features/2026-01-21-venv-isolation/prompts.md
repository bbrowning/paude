# Initial problem described to Claude:

```
Python projects that use a virtual environment cannot be shared between my host and the paude container, right? Because the path to the python binary in a venv is a symlink and that path won't match inside my container. We need to solve this, but I'm not entirely sure what the right solution is. Research, plan, and come up with a set of tasks to fix this. The outcome I want is a python projet cloned locally with a venv (ie .venv dir as an example) works fine on the mac host ANDS works fine from within the paude container. I think this means we can't share the venv between host and container so we need to somehow exclude that from mounting? But how do we know venv dir? Does user have to specify in paude.json? Use your smarts to understand this problem and come up with a great solution for a delightful user experience both in and outside of paude containers for the same project.
```


# Implementation prompt:

```
/ralph-loop:ralph-loop "Implement Python venv isolation for paude.

  ## Goal
  Python projects with virtual environments should work seamlessly both on macOS host AND inside the paude container. The solution: detect venv directories and shadow them with tmpfs mounts so the container gets an empty directory to create its own venv.

  ## Reference
  Read docs/features/2026-01-21-venv-isolation/ for the design. The TASKS.md has acceptance criteria.

  ## Success Criteria
  After each phase, run 'make test' - all tests must pass before moving on.

  ### Phase 1: Detection
  - Create src/paude/venv.py that can detect venv directories by checking for pyvenv.cfg
  - Tests exist and pass

  ### Phase 2: Configuration
  - PaudeConfig supports venv field with modes: 'auto', 'none', or list of dirs
  - paude.json parsing handles the venv field
  - Tests exist and pass

  ### Phase 3: Mount Integration
  - build_venv_mounts() generates tmpfs mounts for detected venvs
  - Runner integrates these mounts after workspace mount
  - User sees 'Shadowing venv: .venv' message when applicable
  - Tests exist and pass

  ### Phase 4: Documentation
  - README.md updated with venv workflow section
  - All tests still pass

  ## If Stuck
  - Read existing code patterns in src/paude/ for style guidance
  - Check test patterns in tests/ for how to structure tests
  - If a test fails, read the error, fix it, re-run

  ## Completion
  - All four phases done
  - make test passes
  - README updated
  " --max-iterations 15
```
