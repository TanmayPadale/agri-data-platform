-- Day 8: mcp_reader, the database login the MCP server uses (ai/mcp_server.py).
--
-- Least privilege, enforced by Postgres rather than by our Python code:
--   it can read the marts schema and nothing else (no raw, staging or snapshots)
--   it cannot write anything, anywhere
--   any query it runs is cancelled after 5 seconds
-- So a bug in a tool, or a prompt-injected request that gets past the tool checks,
-- still cannot change data or read tables the tools were never meant to show.
--
-- The password is for the local Docker database only. Anywhere real, create the role
-- without one and set it from a secret store: ALTER ROLE mcp_reader PASSWORD '...'.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_reader') THEN
        CREATE ROLE mcp_reader LOGIN PASSWORD 'mcp_reader' CONNECTION LIMIT 5;
    END IF;
END $$;

-- Session defaults for every connection this role opens. Read-only by default is a
-- second lock on the door: even a later GRANT INSERT would not make writes succeed
-- unless the client deliberately switched it off.
ALTER ROLE mcp_reader SET default_transaction_read_only = on;
ALTER ROLE mcp_reader SET statement_timeout = '5s';

GRANT USAGE ON SCHEMA marts TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA marts TO mcp_reader;

-- dbt drops and recreates the marts on every build, and a new table starts with no
-- grants. Default privileges give mcp_reader SELECT on every table and view that the
-- role running this file (agri, the same role dbt uses) creates in marts from now on.
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO mcp_reader;
