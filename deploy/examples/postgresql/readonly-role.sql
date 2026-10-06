-- Headscale Easy — read-only PostgreSQL role for the web UI
-- https://github.com/insanerask77/headscale-easy
--
-- The web UI reads the Hostinfo each device reports (OS, Tailscale version,
-- DERP relay, endpoints) from Headscale's database. It gets its own role that
-- can only SELECT those three columns of the "nodes" table: no keys, no other
-- tables, no writes. Idempotent: install.sh and restore.sh run it on every
-- run, connected to Headscale's database as its owner.
--
-- psql variables, from the environment (never on a command line):
--   HSE_RO_USER  role name (e.g. headscale_ro)
--   HSE_RO_PASS  its password
--
--   HSE_RO_USER=headscale_ro HSE_RO_PASS=... psql -v ON_ERROR_STOP=1 \
--       -h <host> -U <headscale owner> -d <database> -f headscale-pg-readonly.sql

\getenv ro_user HSE_RO_USER
\getenv ro_pass HSE_RO_PASS

SELECT format('CREATE ROLE %I LOGIN', :'ro_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'ro_user', :'ro_pass') \gexec
SELECT format('ALTER ROLE %I SET default_transaction_read_only = on', :'ro_user') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'ro_user') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'ro_user') \gexec
-- The table exists once Headscale has started (it creates its schema then)
SELECT format('GRANT SELECT (id, host_info, endpoints) ON TABLE public.nodes TO %I', :'ro_user')
 WHERE to_regclass('public.nodes') IS NOT NULL \gexec
