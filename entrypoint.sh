#!/bin/bash

# Copy claude.json seed file if provided
if [[ -f /tmp/claude.json.seed ]]; then
    cp /tmp/claude.json.seed /home/paude/.claude.json
fi

exec claude "$@"
