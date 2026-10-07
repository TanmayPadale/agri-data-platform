"""Day 8: the MCP server, exercised through a real MCP client.

Most tests connect the client to the server in-process with a fake query function,
so they need no database. The two `db` tests check what only Postgres can prove:
that mcp_reader cannot write or read raw data, and that the real process speaks
clean MCP over stdio.
"""

import asyncio
import os
import sys

import psycopg
import pytest
from mcp import Client, StdioServerParameters
from psycopg.conninfo import make_conninfo

from ai import tools
from ai.mcp_server import SCHEMA_URI, build_server
from ingest.config import REPO_ROOT
from tests.test_tools import DAY, FIELD, FakeQuery


def with_client(server, action):
    """Open an MCP session to `server`, run `action(client)`, return its result."""

    async def run():
        async with Client(server) as client:
            return await action(client)

    return asyncio.run(run())


def call_tool(query, name, arguments):
    return with_client(build_server(query), lambda c: c.call_tool(name, arguments))


def test_tools_are_listed_read_only_with_the_field_id_pattern():
    listed = with_client(build_server(FakeQuery()), lambda c: c.list_tools())
    by_name = {t.name: t for t in listed.tools}
    assert set(by_name) == {"get_field_conditions", "list_fields_to_irrigate"}
    assert all(t.annotations.read_only_hint for t in listed.tools)
    schema = by_name["get_field_conditions"].input_schema
    assert schema["properties"]["field_id"]["pattern"] == tools.FIELD_ID_PATTERN
    assert schema["required"] == ["field_id"]


def test_field_conditions_come_back_structured():
    query = FakeQuery([FIELD], [DAY])
    result = call_tool(query, "get_field_conditions", {"field_id": "F-03", "days": 7})
    assert not result.is_error
    assert result.structured_content["crop"] == "chilli"
    assert result.structured_content["days"][0]["irrigate"] is True
    assert query.calls[1][1] == ("F-03", 7)  # values travel as SQL parameters


@pytest.mark.parametrize("field_id", ["F-01'; DROP TABLE marts.dim_field;--", "3", "f-03"])
def test_bad_field_ids_are_rejected_before_any_sql(field_id):
    query = FakeQuery()
    result = call_tool(query, "get_field_conditions", {"field_id": field_id})
    assert result.is_error
    assert "pattern" in result.content[0].text
    assert query.calls == []  # nothing reached the database


def test_an_unknown_field_is_an_error_the_client_can_read():
    result = call_tool(FakeQuery([]), "get_field_conditions", {"field_id": "F-42"})
    assert result.is_error
    assert "no field F-42" in result.content[0].text


def test_unexpected_errors_do_not_leak_details_to_the_client():
    def broken(sql, params=()):
        raise psycopg.errors.UndefinedTable('relation "marts.dim_field" does not exist')

    result = call_tool(broken, "list_fields_to_irrigate", {})
    assert result.is_error
    assert "dim_field" not in result.content[0].text  # the detail stays in the server log


def test_schema_resource_joins_live_columns_with_the_dbt_docs():
    columns = [
        {"column_name": "irrigate", "data_type": "boolean"},
        {"column_name": "reason", "data_type": "text"},
    ]
    read = with_client(build_server(FakeQuery(columns)), lambda c: c.read_resource(SCHEMA_URI))
    text = read.contents[0].text
    assert text.startswith("# marts.agg_irrigation_signal")
    assert "- irrigate (boolean): true = water it, false = no need" in text


def test_brief_prompt_names_the_region_and_the_tools():
    prompt = with_client(
        build_server(FakeQuery()),
        lambda c: c.get_prompt("irrigation_brief", {"region": "Griffith"}),
    )
    text = prompt.messages[0].content.text
    assert "the Griffith farm" in text
    assert "list_fields_to_irrigate" in text


# ---------------------------------------------------------------- with Postgres


@pytest.fixture
def reader_dsn(test_dsn):
    return make_conninfo(test_dsn, user="mcp_reader", password="mcp_reader")


@pytest.mark.db
def test_mcp_reader_reads_marts_and_nothing_else(test_dsn, reader_dsn):
    with psycopg.connect(test_dsn, autocommit=True) as admin:
        admin.execute("DROP TABLE IF EXISTS marts.mcp_probe")
        admin.execute("CREATE TABLE marts.mcp_probe AS SELECT 1 AS x")  # as a dbt build would
    try:
        with psycopg.connect(reader_dsn, autocommit=True) as conn:
            # A table created after the grants is readable: default privileges at work.
            assert conn.execute("SELECT x FROM marts.mcp_probe").fetchone() == (1,)
            # Lock 1: every transaction starts read-only.
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("INSERT INTO marts.mcp_probe VALUES (2)")
            # Lock 2: a client can switch that default off, but there is no INSERT grant.
            conn.execute("SET default_transaction_read_only = off")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("INSERT INTO marts.mcp_probe VALUES (2)")
            # Raw data is out of reach altogether.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM raw.weather_daily")
            assert conn.execute("SHOW statement_timeout").fetchone() == ("5s",)
    finally:
        with psycopg.connect(test_dsn, autocommit=True) as admin:
            admin.execute("DROP TABLE marts.mcp_probe")


@pytest.mark.db
def test_the_real_process_speaks_mcp_over_stdio(reader_dsn):
    """stdout must carry only protocol messages: one stray print() would break this."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ai.mcp_server"],
        env={**os.environ, "AGRI_MCP_DSN": reader_dsn},
        cwd=str(REPO_ROOT),
    )

    async def session(client):
        return await client.list_tools(), await client.read_resource(SCHEMA_URI)

    listed, schema = with_client(server, session)
    assert len(listed.tools) == 2
    assert "marts.agg_irrigation_signal" in schema.contents[0].text
