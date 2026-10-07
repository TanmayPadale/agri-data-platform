# agri-data-platform

Weather and soil-moisture data in, one decision out: should this field be irrigated today?

This is the capstone of my Stack Refresh Sprint, built one layer a day.

- **Day 1:** daily weather for two farms (Jawali in Satara, India, and Griffith in the
  Riverina, NSW) loads from Open-Meteo into Postgres, validated and idempotent.
- **Day 2:** twenty simulated soil-moisture sensors stream through Kafka. The consumer
  validates every message, parks bad ones in a dead-letter topic, writes with an
  idempotent insert and commits the offset only after the write, so a crash causes
  reprocessing but never a duplicate row.
- **Day 3:** dbt models the raw tables into staging views and marts with a stated grain,
  a field-by-day irrigation signal, 34 tests and an SCD2 snapshot of sensor placements.
- **Day 4:** Airflow 3 runs the daily batch (extract, load, dbt build, quality check) one
  data interval per run, so a 14-day backfill run twice gives identical results. The
  irrigation report is scheduled on data: it runs when the quality check marks the
  signal as updated.
- **Day 5:** one non-root image (170 MB unpacked) for both ingest jobs; Terraform for an
  S3 landing bucket, a least-privilege Lambda and its EventBridge schedule, with remote
  state locked in S3; applied for free against a local AWS emulator; GitHub Actions
  runs lint, tests, dbt, DAG checks, Terraform validation and the image build on every PR.
- **Day 6:** agronomy references (FAO-56, FAO irrigation scheduling, USDA NRCS and UC
  soil-moisture guides) are chunked, embedded locally and stored in pgvector. A small
  local model answers with numbered citations, asks a validated, read-only tool for
  field data, says it does not know when the sources do not cover a question, and
  ignores an instruction planted in one of the sources. Fifteen golden questions score
  retrieval, faithfulness, tool use, refusals and injection resistance, and the judge
  is checked on a known-good and a known-bad answer before its scores count.
- **Day 7:** the consumer exposes RED metrics, a liveness check and a readiness check,
  and runs as two replicas on a local kind cluster (probes, requests and limits, a
  non-root, read-only container) next to the weather load as a CronJob. Prometheus
  scrapes it and Kafka lag, three alert rules have unit tests, Grafana draws a
  provisioned dashboard, and two SLOs (weather freshness, sensor latency) are measured
  in SQL against their error budgets.
- **Day 8:** the same field tools, served over MCP to Claude Code, Claude Desktop or any
  MCP client, with a schema resource built from the dbt docs and a ready-made brief
  prompt. The server logs in as a Postgres role that can read the marts and nothing
  else, with every transaction read-only by default. Tests prove an injected field id
  never reaches SQL and that the role cannot write, even when the client asks it to.

## Quickstart

```bash
cp .env.example .env     # optional, every setting has a default
make up                  # Postgres 17 + pgvector in Docker
make ingest              # last 90 days of weather for both farms
make queries             # five analytical queries over the result

make up-stream           # + Kafka 4 (KRaft) and Redis, topics created
make history             # 21 days of past sensor readings into Kafka
make consume             # Kafka -> raw.sensor_readings (Ctrl+C stops)

make dbt-build           # staging, marts, snapshot and every test
make signal              # latest irrigation decision for every field

make down && make up     # on 8 GB, never run Kafka and Airflow together
make airflow-install     # once: Airflow 3.3 in its own venv
make airflow             # UI on http://localhost:8080 (password in .airflow/)
make backfill FROM=2026-09-21 TO=2026-10-04

make image               # the ingest image, smoke-tested
make tf-validate         # what CI runs: fmt + validate, no credentials
make tf-emulator-apply   # the AWS side in LocalStack, free (make tf-emulator-destroy after)

ollama pull llama3.2:3b && ollama pull nomic-embed-text   # once: free local models
make docs-fetch          # download the agronomy references (not committed)
make docs-ingest         # chunk, embed and store them in pgvector
make ask Q="What is the mid-season crop coefficient for sweet peppers?"
make evals               # the 15 golden questions, scored

make up-monitoring       # Prometheus :9090, Grafana :3000, kafka-exporter (after up-stream)
make consume             # now also serves /metrics, /healthz and /ready on :8000
make metrics             # health, readiness and the counters
make poison              # then watch SensorMessagesInDLQ fire on localhost:9090/alerts
make slo                 # the two SLIs against their targets
make alerts-check        # promtool: config, rules and the alert unit tests
make k8s-validate        # manifests against the Kubernetes 1.37 schemas
make verify-mac          # kind: build, load, deploy two replicas and the CronJob, check

make ddl                 # also creates the read-only mcp_reader role
make mcp-check           # MCP tests: tools, injection, the read-only role, a real stdio session
make mcp-inspect         # the MCP Inspector in your browser (needs Node)
make test
```

## Use the farm data from Claude (Day 8)

**Claude Code:** open this folder. `.mcp.json` registers the `agri-data` server; approve
it when asked, then try "Which fields need irrigation today, and why?". To add it by
hand instead:

```bash
claude mcp add --env AGRI_MCP_DSN=postgresql://mcp_reader:mcp_reader@localhost:5432/agri \
  agri-data -- uv run --directory /absolute/path/to/agri-data-platform --quiet python -m ai.mcp_server
```

**Claude Desktop:** add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
and restart the app. Desktop starts servers with a minimal PATH, so use the full path
that `which uv` prints.

```json
{
  "mcpServers": {
    "agri-data": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "/absolute/path/to/agri-data-platform", "--quiet",
               "python", "-m", "ai.mcp_server"]
    }
  }
}
```

Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
