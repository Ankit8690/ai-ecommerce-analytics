#!/bin/bash
# Runs ONCE on first Postgres container init (via docker-entrypoint-initdb.d).
# Creates the application database and the two dedicated roles used by the
# app. Real schema/data load is done separately by the `db-init` compose
# profile which invokes database/seed.py.

set -euo pipefail

: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"
: "${READONLY_DB_PASSWORD:?READONLY_DB_PASSWORD must be set}"

echo "[init-postgres] creating roles and ecommerce_ai database..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Roles
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ecommerce_app') THEN
            CREATE ROLE ecommerce_app WITH LOGIN PASSWORD '${APP_DB_PASSWORD}';
        ELSE
            ALTER ROLE ecommerce_app WITH LOGIN PASSWORD '${APP_DB_PASSWORD}';
        END IF;

        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ecommerce_readonly') THEN
            CREATE ROLE ecommerce_readonly WITH LOGIN PASSWORD '${READONLY_DB_PASSWORD}';
        ELSE
            ALTER ROLE ecommerce_readonly WITH LOGIN PASSWORD '${READONLY_DB_PASSWORD}';
        END IF;
    END
    \$\$;

    -- Database (created only if absent — Postgres has no CREATE DATABASE IF NOT EXISTS,
    -- so we check pg_database).
    SELECT 'CREATE DATABASE ecommerce_ai OWNER ecommerce_app'
    WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'ecommerce_ai')\gexec
EOSQL

# Grant CONNECT on the new database so both roles can log in.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    GRANT CONNECT ON DATABASE ecommerce_ai TO ecommerce_app;
    GRANT CONNECT ON DATABASE ecommerce_ai TO ecommerce_readonly;
EOSQL

echo "[init-postgres] done. Load data with:  docker compose --profile init up db-init"
