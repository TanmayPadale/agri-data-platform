"""Day 8: the farm data as an MCP server.

Any MCP client (Claude Code, Claude Desktop, the MCP Inspector) can then ask the
same questions the Day 6 assistant answers, through the same two functions:

    uv run python -m ai.mcp_server            # speaks MCP over stdin and stdout
    npx @modelcontextprotocol/inspector uv run python -m ai.mcp_server   # try it in a browser

What it offers the client:
    tools     get_field_conditions(field_id, days)   one field's recent days
              list_fields_to_irrigate()              the latest decisions, all fields
    resource  agri://marts/irrigation-signal/schema  what each column means (from dbt)
    prompt    irrigation_brief(region)               a ready-made morning brief request

Three layers of defence, from the outside in:
    1. arguments are checked against a JSON schema before any code runs
       (field ids must look like F-03; days is 1 to 30)
    2. the tools run the fixed, parameterised SQL in ai/tools.py: a client never sends SQL
    3. the connection logs in as mcp_reader (sql/roles/mcp_reader.sql), so Postgres
       itself refuses writes and anything outside the marts schema

stdout belongs to the protocol, and a single stray print() would corrupt it. Logs go
to stderr, where MCP clients collect them.
"""

from __future__ import annotations

import logging
import os
import sys
from functools import partial
from typing import Annotated

import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from psycopg.conninfo import conninfo_to_dict
from pydantic import Field

from ai import tools
from ingest.config import REPO_ROOT

log = logging.getLogger("agri.mcp")

DEFAULT_DSN = "postgresql://mcp_reader:mcp_reader@localhost:5432/agri"
MARTS_DOCS = REPO_ROOT / "transform" / "models" / "marts" / "_marts.yml"
SCHEMA_URI = "agri://marts/irrigation-signal/schema"

INSTRUCTIONS = """Read-only farm data for two farms: Jawali (Medha) in Satara, India, \
with fields F-01 to F-05, and Griffith in NSW, Australia, with fields F-06 to F-10. \
Every field has soil-moisture sensors and daily weather. Use get_field_conditions for \
one field's recent days and list_fields_to_irrigate for today's decisions across all \
fields. Read the agri://marts/irrigation-signal/schema resource for what each value means."""

# Hints for the client: these tools only read, give the same answer when repeated,
# and touch nothing outside this database. Clients may skip a confirmation prompt.
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


def mcp_dsn() -> str:
    return os.environ.get("AGRI_MCP_DSN", DEFAULT_DSN)


def describe_dsn(dsn: str) -> str:
    """user@host/db for the log line, without the password."""
    parts = conninfo_to_dict(dsn)
    return f"{parts.get('user')}@{parts.get('host')}/{parts.get('dbname')}"


def schema_doc(query: tools.Query) -> str:
    """Column names and types from Postgres (always current) plus their meaning from
    the dbt docs in _marts.yml (the same text `make dbt-docs` shows)."""
    columns = query(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'marts' AND table_name = 'agg_irrigation_signal'
        ORDER BY ordinal_position
        """,
        (),
    )
    model = next(
        m
        for m in yaml.safe_load(MARTS_DOCS.read_text())["models"]
        if m["name"] == "agg_irrigation_signal"
    )
    meaning = {c["name"]: " ".join(c.get("description", "").split()) for c in model["columns"]}
    lines = [
        "# marts.agg_irrigation_signal",
        "",
        " ".join(model["description"].split()),
        "",
    ]
    for c in columns:
        note = meaning.get(c["column_name"], "")
        lines.append(f"- {c['column_name']} ({c['data_type']})" + (f": {note}" if note else ""))
    return "\n".join(lines)


def build_server(query: tools.Query | None = None) -> MCPServer:
    """Tests pass a fake query function. The real server reads as mcp_reader."""
    query = query or partial(tools.run_query, dsn=mcp_dsn())
    server = MCPServer("agri-data", instructions=INSTRUCTIONS)

    @server.tool(annotations=READ_ONLY)
    def get_field_conditions(
        field_id: Annotated[
            str, Field(pattern=tools.FIELD_ID_PATTERN, description="Field id, for example F-03")
        ],
        days: Annotated[int, Field(ge=1, le=30, description="How many recent days")] = 7,
    ) -> tools.FieldConditions:
        """Recent daily rain (mm), maximum temperature, soil moisture (%) and the
        irrigation decision with its reason, for one field."""
        try:
            return tools.get_field_conditions(field_id, days, query=query)
        except tools.UnknownField as exc:
            # ToolError: an expected failure, so the client sees this message. Any other
            # exception reaches the client only as "Error executing tool", and the
            # details (which may include SQL) stay in this server's log.
            raise ToolError(str(exc)) from exc

    @server.tool(annotations=READ_ONLY)
    def list_fields_to_irrigate() -> tools.IrrigationList:
        """Every field flagged for irrigation on the latest day that has decisions,
        with the soil moisture and rain behind each decision."""
        return tools.list_fields_to_irrigate(query=query)

    @server.resource(SCHEMA_URI, mime_type="text/markdown")
    def irrigation_signal_schema() -> str:
        """What each column of the irrigation signal means, with its type."""
        return schema_doc(query)

    @server.prompt()
    def irrigation_brief(
        region: Annotated[str, Field(description="Jawali, Griffith or all")] = "all",
    ) -> str:
        """A short morning brief: which fields need water today, and why."""
        scope = "every field" if region.strip().lower() == "all" else f"the {region} farm"
        return (
            f"Write a short irrigation brief for {scope}. Call list_fields_to_irrigate, "
            "then get_field_conditions for each field it returns. For each field give "
            "one line: the field, its crop, the 3-day average soil moisture against the "
            "threshold, the rain, and the decision. Use only numbers the tools return. "
            "End with any field whose decision is unknown and why."
        )

    return server


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # never stdout: that is the protocol channel
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("agri-data MCP server on stdio, reading as %s", describe_dsn(mcp_dsn()))
    build_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
