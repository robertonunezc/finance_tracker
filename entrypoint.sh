#!/bin/bash

set -e

echo "Waiting for database..."
DB_HOST=${POSTGRES_HOST:-postgres}
DB_PORT=${POSTGRES_PORT:-5432}
echo "Waiting for database at ${DB_HOST}:${DB_PORT}..."
while ! nc -z "$DB_HOST" "$DB_PORT"; do
    sleep 1
done
echo "Database is ready!"

echo "Running database migrations..."
python manage.py migrate --noinput 

echo "Collecting static files..."
python manage.py collectstatic --noinput

echo "Starting Gunicorn server..."
exec gunicorn --bind 0.0.0.0:${WEB_PORT:-8000} --workers ${GUNICORN_WORKERS:-3} finance_tracker.wsgi:application
