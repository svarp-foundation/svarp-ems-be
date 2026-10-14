#!/bin/bash

# Script to handle database migrations using Alembic for portal-exam backend
# Usage: 
#   ./scripts/migrate.sh create "migration message"  - To create a new migration
#   ./scripts/migrate.sh apply                        - To apply migrations to the database
#   ./scripts/migrate.sh stamp                        - To stamp the database with the current head

set -e

# Navigate to the backend directory (where alembic.ini is located)
cd "$(dirname "$0")/.."

# Check if venv exists and activate it
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d "../venv" ]; then
    source ../venv/bin/activate
else
    echo "Warning: venv not found. Using system python/alembic."
fi

COMMAND=$1

if [ "$COMMAND" == "create" ]; then
    MESSAGE=$2
    if [ -z "$MESSAGE" ]; then
        echo "Error: Migration message is required."
        echo "Usage: ./scripts/migrate.sh create \"migration message\""
        exit 1
    fi
    echo "Generating new migration: $MESSAGE"
    alembic revision --autogenerate -m "$MESSAGE"
elif [ "$COMMAND" == "apply" ]; then
    echo "Applying migrations to the database..."
    MIGRATE_LOG=$(mktemp)
    if ! alembic upgrade heads >"$MIGRATE_LOG" 2>&1; then
        if grep -q "already exists" "$MIGRATE_LOG"; then
            echo "Pre-existing database detected. Stamping database with head revision..."
            alembic stamp head
            echo "Re-applying migrations..."
            alembic upgrade heads
        else
            cat "$MIGRATE_LOG"
            rm -f "$MIGRATE_LOG"
            exit 1
        fi
    else
        cat "$MIGRATE_LOG"
    fi
    rm -f "$MIGRATE_LOG"
elif [ "$COMMAND" == "stamp" ]; then
    echo "Stamping the database with the current head..."
    alembic stamp head
else
    echo "Invalid command: $COMMAND"
    echo "Usage:"
    echo "  ./scripts/migrate.sh create \"message\""
    echo "  ./scripts/migrate.sh apply"
    echo "  ./scripts/migrate.sh stamp"
    exit 1
fi
