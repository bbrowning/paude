# Orchestration Workflow

Paude supports a fire-and-forget workflow: assign the agent a task, monitor progress, harvest the changes into a branch, and open a PR — all without connecting to the session.

## Assign a Task

Create a session with `--git` to push your code, then give the agent a task with `-a`:

```bash
cd your-project
paude create --yolo --git my-project -a '-p "refactor the auth module"'
```

The agent works autonomously inside the container. You can disconnect and come back later.

## Monitor Progress

Check what the agent is doing with `paude status`:

```bash
paude status
```

```
SESSION     PROJECT        BACKEND   STATUS    ACTIVITY              STATE
my-project  your-project   podman    running   2026-03-08 14:32:01   Active
```

The `STATE` column shows `Active` when the agent is working or `Idle` when waiting.

## Harvest Changes

When the agent finishes (or you want to review progress), pull the changes into a local branch:

```bash
paude harvest my-project -b feature/auth-refactor
```

This creates a local `feature/auth-refactor` branch with all of the agent's commits. Review the diff, run tests, and iterate as needed.

Protected branches (`main`, `master`, `release`) cannot be used as harvest targets.

## Open a PR

Once you're satisfied with the changes, harvest again with `--pr` to push the branch and create a pull request:

```bash
paude harvest my-project -b feature/auth-refactor --pr
```

This pushes `feature/auth-refactor` to origin and runs `gh pr create`.

## Reset and Repeat

After the PR merges, reset the remote session to prepare for the next task:

```bash
paude reset my-project
```

Reset performs a `git reset --hard` to `origin/main` and `git clean -fdx` inside the container, and clears conversation history. Use `--keep-conversation` to preserve history across tasks.

If the agent has unmerged work, reset warns you. Use `--force` to proceed anyway.

Then assign the next task — connect and type your prompt, or stop and recreate with `-a`:

```bash
paude connect my-project
# Or, for a fully autonomous run:
paude stop my-project
paude create --yolo --git my-project -a '-p "add rate limiting to the API"'
```
