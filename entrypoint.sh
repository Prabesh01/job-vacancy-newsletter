#!/bin/sh
set -e

if [ ! -f /app/data/db.sqlite3 ]; then
    cp db.sqlite3 /app/data/db.sqlite3
fi

exec "$@"
