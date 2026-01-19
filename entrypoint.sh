#!/bin/bash

# Copy seed files if provided (mounted read-only from host)
if [[ -d /tmp/claude.seed ]]; then
    cp -r /tmp/claude.seed/. /home/paude/.claude/
fi

if [[ -f /tmp/claude.json.seed ]]; then
    cp /tmp/claude.json.seed /home/paude/.claude.json
fi

exec claude "$@"
