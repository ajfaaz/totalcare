#!/bin/bash
set -euo pipefail

LIVE_DIR="/home/jvlbvywb/totalcare.arewanetventures.com"

cd "$LIVE_DIR"

echo "Running Django health checks..."
python manage.py check

echo "Checking core app files..."
test -f "$LIVE_DIR/.htaccess"
test -f "$LIVE_DIR/passenger_wsgi.py"
test -f "$LIVE_DIR/manage.py"

echo "Checking staticfiles directory..."
test -d "$LIVE_DIR/staticfiles"

echo "Health check passed."
