#!/bin/bash

# Exit immediately if any command exits with a non-zero status
set -e

echo "=== Starting Backend Deployment ==="

# Navigate to the backend directory (where this script is located)
cd "$(dirname "$0")"

# 1. Pull latest changes from dev branch
echo "Pulling latest changes from origin dev..."
git pull origin dev

# 2. Check and install python dependencies
if [ -f "requirements.txt" ]; then
    if [ -d "../venv" ]; then
        echo "Checking and installing python packages..."
        ../venv/bin/pip install -r requirements.txt
    elif [ -d "venv" ]; then
        echo "Checking and installing python packages..."
        ./venv/bin/pip install -r requirements.txt
    else
        echo "Warning: virtual environment not found, skipping package installation."
    fi
fi

# 3. Apply database migrations
if [ -f "./scripts/migrate.sh" ]; then
    echo "Running database migrations..."
    ./scripts/migrate.sh apply
else
    echo "Warning: migrate.sh not found, skipping database migrations."
fi

# 4. Restart the backend systemd service
echo "Restarting the backend service..."
sudo systemctl restart exam-portal-be || sudo systemctl restart portal-exam-be

echo "=== Backend Deployment Completed Successfully ==="
