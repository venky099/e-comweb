#!/usr/bin/env bash
#
# Deployment script for an EC2 / VM host running Gunicorn behind Nginx.
#
#   ssh host 'cd /srv/ecommerce && ./deploy/deploy.sh'
#
# Assumes: the repo at /srv/ecommerce, a virtualenv at ./venv, and a .env file
# holding production settings (never committed).
#
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/ecommerce}"
VENV="${VENV:-$APP_DIR/venv}"
SERVICE="${SERVICE:-ecommerce}"

cd "$APP_DIR"

echo "==> Pulling latest code"
git pull --ff-only

echo "==> Installing dependencies"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r requirements.txt

export DJANGO_SETTINGS_MODULE=config.settings.prod

echo "==> Checking configuration"
# --deploy runs Django's production security checklist; --fail-level ERROR
# stops the deploy on anything genuinely unsafe.
"$VENV/bin/python" manage.py check --deploy --fail-level ERROR

echo "==> Applying database migrations"
"$VENV/bin/python" manage.py migrate --noinput

echo "==> Collecting static files"
"$VENV/bin/python" manage.py collectstatic --noinput --clear

echo "==> Reloading the application server"
sudo systemctl reload-or-restart "$SERVICE"

echo "==> Waiting for the app to come back"
for _ in $(seq 1 15); do
    if sudo systemctl is-active --quiet "$SERVICE"; then
        echo "    service is active"
        break
    fi
    sleep 1
done

echo "==> Reloading Nginx"
sudo nginx -t && sudo systemctl reload nginx

echo "==> Done"
