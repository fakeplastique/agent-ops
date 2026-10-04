-- Run once as an admin role before migrations; idempotent. A single statement: it is sent as one prepared call.
-- app_rt: dev password, replaced per environment (APP_RT_PASSWORD, see scripts/migrate.sh).
-- app_worker: scans for due work across tenants (ids only, see migration 0007). NOLOGIN until an environment
-- provisions a password: no worker identity exists by default, and production gets none from this file.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_rt') THEN
    CREATE ROLE app_rt LOGIN PASSWORD 'app_rt' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_worker') THEN
    CREATE ROLE app_worker NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
END $$;
