#!/bin/bash
# Legacy entrypoint - redirect to entrypoint-session.sh
# All sessions now use the persistent session model
exec /usr/local/bin/entrypoint-session.sh "$@"
