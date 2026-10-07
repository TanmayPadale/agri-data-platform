# agri-data-platform

Weather and soil-moisture data in, one decision out: should this field be irrigated today?

This is the capstone of my Stack Refresh Sprint, built one layer a day and tagged
`day-1` to `day-8`. I am now studying it backwards, and [STUDY.md](STUDY.md) is the
route I use: start from the answer, trace every number to its source, then take one
day apart per sitting.

## The answer it produces

From the verification run on 7 Oct 2026 (simulated sensors, real Open-Meteo weather),
asked through the MCP server from Claude Code:

```text
list_fields_to_irrigate  ->  day 2026-10-06
  F-03  chilli       Jawali (Medha)  3-day moisture 23.23% is below 25% and rain 0.00 mm is below 2 mm
  F-05  tomato       Jawali (Medha)  3-day moisture 24.76% is below 25% and rain 0.00 mm is below 2 mm
  F-07  cotton       Griffith        3-day moisture 24.84% is below 25% and rain 0.00 mm is below 2 mm
  F-09  wine grapes  Griffith        3-day moisture 20.65% is below 25% and rain 0.00 mm is below 2 mm
```

For 7 Oct the same fields read `irrigate = null, "unknown: no weather for this day yet"`.
The platform says "unknown" instead of guessing when an input is missing.

## How the data moves

```mermaid
flowchart LR
    OM["Open-Meteo<br/>daily weather, 2 farms"] --> W["ingest/weather.py<br/>validate, upsert"]
    AF["Airflow 3<br/>agri_daily"] -. "runs the<br/>daily batch" .-> W
    S["20 simulated sensors<br/>a reading every 2 s"] --> K[("Kafka 4, KRaft<br/>6 partitions")]
    K --> C["sensor-consumer<br/>validate, dedupe,<br/>write, commit"]
    C -- invalid --> D[("dead-letter<br/>topic")]
    C <--> R[("Redis<br/>seen ids, latest")]
    DOCS["10 agronomy references<br/>FAO, USDA NRCS, UC ANR"] --> I["ai/ingest_docs.py<br/>chunk, embed"]
    subgraph PG["Postgres 17 + pgvector"]
        RAW["raw tables"] --> DBT["dbt: staging, intermediate,<br/>marts, 34 data tests"]
        DBT --> SIG["agg_irrigation_signal<br/>true, false or unknown"]
        VEC["doc_chunks<br/>1,200 chunks"]
    end
    W --> RAW
    C --> RAW
    I --> VEC
    SIG --> REP["irrigation report<br/>runs on an Asset"]
    SIG --> T["get_field_conditions<br/>validated, read-only"]
    T --> ASK["RAG assistant<br/>cites or refuses"]
    VEC --> ASK
    T --> MCP["MCP server<br/>as mcp_reader"]
    MCP --> CL["Claude Code,<br/>Claude Desktop"]
```

Around it sit the platform pieces: one Docker image for both ingest jobs, Terraform for
the AWS side (applied for free to LocalStack), five CI jobs on every pull request, a
kind cluster for the consumer and the weather CronJob, and Prometheus with Grafana for
the stream's health.

## What each day added

- **Day 1:** daily weather for two farms (Jawali in Satara, India, and Griffith in the
  Riverina, NSW) loads from Open-Meteo into Postgres, validated and idempotent. Each
  farm's "day" is its own local calendar day.
- **Day 2:** twenty simulated soil-moisture sensors stream through Kafka. The consumer
  validates every message, parks bad ones in a dead-letter topic, writes with an
  idempotent insert and commits the offset only after the write, so a crash causes
  reprocessing but never a duplicate row.
- **Day 3:** dbt models the raw tables into staging views, two intermediate models and
  marts with a stated grain: a field-by-day irrigation signal over a date spine, 34
  data tests and an SCD2 snapshot of which field each sensor was on, and when.
- **Day 4:** Airflow 3 runs the daily batch (extract, load, dbt build, quality check)
  one data interval per run, so a 14-day backfill run twice gives identical results.
  The irrigation report is scheduled on data: it runs when the quality check marks the
  signal as updated.
- **Day 5:** one non-root image for both ingest jobs; Terraform for an S3 landing
  bucket, a least-privilege Lambda and its EventBridge schedule, with remote state
  locked in S3, applied for free to LocalStack; GitHub Actions runs lint, tests, dbt,
  DAG checks, Terraform validation, the image build and the manifest checks on every PR.
- **Day 6:** ten agronomy references (FAO-56, FAO irrigation scheduling, USDA NRCS and
  UC soil-moisture guides) are chunked, embedded locally and stored in pgvector. A small
  local model answers with numbered citations, asks a validated, read-only tool for
  field data, says it does not know when the sources do not cover a question, and
  ignores an instruction planted in one of the sources. Fifteen golden questions score
  retrieval, faithfulness, tool use, whether tool answers match the tool's output,
  refusals and injection resistance, and the judge is checked on a known-good and a
  known-bad answer before its scores count.
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

![The Grafana dashboard during the Day 7 labs](docs/grafana-dashboard.png)

*The provisioned dashboard during the Day 7 labs: one poison message in the DLQ panel,
p95 processing time under 20 ms, and a burst that builds lag on all six partitions and
drains back to zero.*

## Quickstart

Needs Docker (OrbStack works well on a Mac), [uv](https://docs.astral.sh/uv/), and for
Day 6, [Ollama](https://ollama.com/). Every setting has a default; `.env.example` lists them.

```bash
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
make evals               # the 15 golden questions, scored (31 minutes on a CPU-only machine)

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

## What I measured

Everything below was run on 7 Oct 2026. The "how" column is how to repeat it.

| What | Result | How |
|---|---|---|
| Weather load is idempotent | 90 days for 2 farms is 180 rows, and still 180 after a second load | `make ingest` twice, then count |
| Crash safety | 62,840 rows after the crash, poison and rebalance labs, zero duplicates | STUDY.md, Day 2 labs |
| dbt | 47 nodes pass: 10 models, 2 seeds, 1 snapshot, 34 data tests | `make dbt-build` |
| Backfill | 14 days, run twice, identical table fingerprint | STUDY.md, Day 4 lab 1 |
| Consumer image | 171 MB unpacked, runs as a non-root user (uid 10001) | `make image`, CI `image` job |
| AWS side, for free | 12 resources applied in LocalStack, Lambda invoked, drift detected, a second plan stopped by the state lock, clean destroy | `make tf-emulator-apply`, `make tf-emulator-run` |
| Retrieval corpus | 1,200 chunks from 10 references, every table row its own chunk | `make docs-ingest` |
| Assistant evals | hit@5 10/10, faithfulness 9/10, tool use 3/3, tool answers supported 3/3, refusals 2/2, injection resisted 1/1, with llama3.2:3b and a calibrated judge, in 31 minutes | `make evals`, baseline in `ai/evals/baseline.json` |
| DLQ alert | fired 25 seconds after one poison message | `make poison`, then the alerts page |
| Tests | 99 pytest tests pass, plus 8 DAG tests in Airflow's own venv; ruff is clean; the same in a fresh clone against an empty database | `make test`, `make test-dags` |
| MCP from Claude Code | opening the repo started the server from `.mcp.json`; both tools returned the rows shown above | open the repo in Claude Code |

## Service levels

Two SLOs say whether the platform is doing its job. Both are SQL in `sql/slo/`.

| SLI | SLO, 30 days | Error budget |
|---|---|---|
| Both farms' weather for day D first loaded by 01:00 UTC on D+1 | 99% of days | about 0.3 days |
| Live sensor readings stored within 60 s of being measured | 99.5% | 0.5% of readings |

The first measurement, on 7 Oct 2026, is worth reading closely:

- **Freshness: 0 of 30 days on time.** Every weather row in this environment was first
  loaded on 7 Oct, in backfills, so none of them arrived by 01:00 UTC the next day. The
  SLI is right to say so: a backfill is not on-time delivery. That is why it uses
  `first_loaded_at`, which an upsert never touches, instead of `loaded_at`, which moves
  on every reload. Real days start counting from the first scheduled run.
- **Latency: 98.1% of 5,952 readings within 60 s, against a target of 99.5%.** History
  replays are marked with a Kafka header at the producer and stored as `replayed`, so
  they never count as slow live traffic. All 112 slow readings turned out to be the last
  hour of a Day 2 history replay, sent before that header existed. The one-off migration
  that added the column guessed from age (stored more than an hour after it was measured
  means replay), and these were 10 to 60 minutes old, so the guess counted them as live.
  Every reading sent after the header existed met the target, with p95 at 0.56 s in a
  steady hour. A database built fresh from this repo has the header and the column from
  the start, so it never needs the guess.

## Design decisions

- **Kafka over a plain queue** for the sensors: replay from any offset, ordering per
  sensor key, and room for more than one consumer group.
- **At-least-once delivery into an idempotent sink** over Kafka transactions: the sink is
  Postgres, outside Kafka's transactions, and an insert that ignores the natural key's
  duplicates absorbs producer retries, crashes and replays in one mechanism.
- **The primary key is the guarantee, Redis is only a fast path.** If Redis lost every
  key, results would still be right, just slower.
- **Unknown is not false.** A missing weather row makes `irrigate` NULL with a reason.
  Treating missing rain as 0 mm would water fields on rain nobody has measured yet.
- **Three calendar days, really.** A date spine and a `range` window over dates, so a
  missing day shortens the average instead of quietly stretching it to four days.
- **Each run owns one interval.** `CronDataIntervalTimetable` instead of `"@daily"`,
  which in Airflow 3 gives runs no real data interval, and the report's Asset is emitted
  by the quality check, so it never runs on data that failed.
- **LocalStack over a real AWS account** for the sprint: the same Terraform, zero cost,
  and switching to real AWS is a backend file and a variable.
- **pgvector next to the marts** over a separate vector database: one Postgres to back
  up, SQL filters beside similarity search, and nothing extra to run on 8 GB.
- **Narrow tools over text-to-SQL.** The model gets two task-shaped tools with validated
  inputs, and the login behind them can only read the marts.
- **Code does the arithmetic, the model gets sentences.** The first full eval scored tool
  use 3/3, yet reading the answers showed the 3B model telling me a dry field needed no
  water. The tool now returns the decision as a sentence worked out in code, and the
  eval judges every tool answer against the tool's output.

## Scaling it to 10,000 farms

Start with arithmetic, not tools. With 20 sensors per farm, as here, that is 200,000
sensors, and a JSON reading is about 150 bytes.

| Sampling | Messages per second | Rows per day | Raw data per day |
|---|---|---|---|
| One reading every 5 minutes | 200,000 / 300 = about 667 | about 57.6 million | about 8.6 GB |
| One every 2 seconds, as now | 200,000 / 2 = 100,000 | about 8.64 billion | about 1.3 TB |

The 150-fold gap comes from the sampling rate alone, and soil moisture changes slowly,
so that is the first question to ask. Then: gateways and MQTT in front of a managed,
replicated Kafka; consumers autoscaled on lag, writing in batches; raw data in object
storage with a table format or a warehouse; incremental dbt there; weather fetched per
grid cell instead of per farm. Natural keys with idempotent writes, the contracts at the
boundary, the dead-letter topic, the grain tests and the tool interface all stay the same.

## Known limits

- The rule uses the same day's rain. A farmer would act on the forecast for the next
  two days, which Open-Meteo also provides.
- Readings land in seconds, but the signal only changes when dbt runs, once a day. The
  live value per sensor is in Redis (`latest:{sensor_id}`) for anything that needs it now.
- One Kafka broker with replication factor 1 is for learning only. Production wants three
  brokers, replication factor 3 and `min.insync.replicas` 2.
- The S3 landing bucket is a dead end so far. A warehouse stage or an Iceberg table over
  the prefix would be next.
- Prometheus reaches the kind pods through one port-forward, which picks a single pod.
  In-cluster Prometheus with pod discovery would see both replicas.
- The eval judge is the same 3B model it grades. Calibration catches a judge that is
  plainly broken, not one that is subtly lenient.
- LocalStack is an emulator. A policy that works there still needs one apply against
  real AWS before I would trust it.

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

## Troubleshooting

- **`make evals` stops with a 500 from Ollama.** On 8 GB this is usually memory: Ollama's
  model worker keeps past prompts in a RAM cache that may grow to 8 GB, and the system
  kills the worker. The client retries once, and the retry gets a fresh worker. To stop it
  happening, cap the cache: `LLAMA_ARG_CACHE_RAM=1024 ollama serve` (checked with Ollama
  0.40, whose log then says "size limit: 1024 MiB"). For the Ollama Mac app, set it with
  `launchctl setenv LLAMA_ARG_CACHE_RAM 1024` and restart the app, the way Ollama's FAQ
  sets its own variables.
- **The first `make ask` after a restart is slow.** Loading the model from a cold disk took
  over a minute here. Later questions reuse it.
- **Port 5432 is taken.** Map Postgres to `"5433:5432"` in `docker-compose.yml` and put
  the new port in `AGRI_DSN` in `.env`.
- **Everything slows down on 8 GB.** Run one heavy thing at a time: Kafka or Airflow,
  never both. `make down` stops the containers.

## Repo map

```text
ingest/        weather and sensor ingest, the consumer, metrics and health (Days 1, 2, 7)
seeds/         the two farms, shared by the ingest code, dbt and Terraform
sql/           schemas, raw tables, five queries, the SLIs, the read-only MCP role
transform/     dbt: staging, intermediate and marts, tests, the SCD2 snapshot (Day 3)
dags/          agri_daily and the Asset-triggered irrigation_report (Day 4)
infra/         Terraform: bucket module, Lambda, IAM, schedule, state bootstrap (Day 5)
ai/            references, ingest, retrieval, tools, the assistant, evals, MCP (Days 6, 8)
k8s/           the consumer Deployment, the weather CronJob, kind config (Day 7)
monitoring/    Prometheus, alert rules and their tests, Grafana (Day 7)
tests/         one test file per module, with fakes for Kafka and Open-Meteo
scripts/       Airflow's venv helper, the Mac deployment check
docs/          the dashboard screenshot
STUDY.md       how to learn this repo backwards, one day tag at a time
```

## Credits

- Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0). The test fixtures
  are real responses, recorded on 7 Oct 2026.
- The agronomy references are published by FAO, USDA NRCS and UC Agriculture and Natural
  Resources. `make docs-fetch` downloads them from their publishers (listed in
  `ai/sources.json`); they are not stored in this repo, and their publishers keep the copyright.
- Local models through Ollama: Llama 3.2 3B for answers and nomic-embed-text for embeddings.
