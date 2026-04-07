#!/bin/bash
set -euo pipefail

REPO_DIR="/home/jvlbvywb/repositories/totalcare"
LIVE_DIR="/home/jvlbvywb/totalcare.arewanetventures.com"

echo "Pulling latest code from GitHub..."
cd "$REPO_DIR"
git pull origin main

echo "Copying application files into live directory..."
tar \
  --exclude=".git" \
  --exclude=".env" \
  --exclude="env" \
  --exclude="db.sqlite3" \
  --exclude="public" \
  --exclude="staticfiles" \
  --exclude="stderr.log" \
  --exclude="__pycache__" \
  -cf - . | (cd "$LIVE_DIR" && tar -xf -)

echo "Running Django deployment steps..."
cd "$LIVE_DIR"
python manage.py migrate
python manage.py collectstatic --noinput
touch tmp/restart.txt

echo "Deployment complete."
