# Study guide: learn this platform by taking it apart

I am learning this codebase backwards: start from the answer it produces, trace
where every number came from, then rebuild each layer in my head one day at a time.
This guide is the route.

Each day below has the same five parts:

1. **Read**: the files in the order that tells the story, with what to look for.
2. **Run**: the commands that show the layer working.
3. **Break**: labs that break something on purpose. Predict what will happen first,
   then run it, then put it back with `git checkout <file>`.
4. **Explain**: questions to answer out loud without looking. If I cannot, I reread.
5. **Interview angle**: the one story from this layer worth telling.

The comments in the code are part of the lesson. Most files open with a docstring
that says what the file is for and why it is shaped that way.

## Moving through time with the day tags

Every day ends with a git tag, so each layer can be seen on its own:

```bash
git tag -l                          # day-1 ... day-8
git log --oneline day-3..day-4      # the commits that made Day 4
git diff --stat day-3 day-4         # which files Day 4 touched
git checkout day-2                  # the repo exactly as it was after Day 2
git checkout main                   # back to the present
```

`git diff day-3 day-4 -- dags/` is a good way to read a day: only what changed.

## Start at the end: the reverse trace

One question drives the whole project: should field F-03 be irrigated today?
Follow that answer backwards, one hop at a time:

| Hop | Where | What to notice |
|---|---|---|
| 1 | `make signal` | The decision for every field, with its reason in words |
| 2 | `transform/models/marts/agg_irrigation_signal.sql` | The rule, the date spine, and why missing data gives NULL |
| 3 | `transform/models/intermediate/int_field_daily_moisture.sql` | Sensor hours become local farm days |
| 4 | `transform/models/intermediate/int_sensor_field_history.sql` | Which field a sensor was on at each hour |
| 5 | `transform/snapshots/sensors_snapshot.yml` | Where that history comes from (SCD2) |
| 6 | `transform/models/marts/fct_sensor_hourly.sql` | Readings rolled up per hour, built incrementally |
| 7 | `transform/models/staging/stg_sensor_readings.sql` | Renames, types, the second dedupe |
| 8 | `sql/ddl/raw_sensor_readings.sql` | The primary key that makes duplicates impossible |
| 9 | `ingest/sensor_consumer.py` | Validate, dedupe, write, then commit |
| 10 | `ingest/sensor_producer.py` | The simulated sensors and the event id |

Then do the same for rain: `fct_daily_weather.sql`, `stg_weather.sql`,
`sql/ddl/raw_weather_daily.sql`, `ingest/weather.py`, `seeds/locations.csv`.

After the trace, every day below will feel like filling in detail on a map I
already have.

---

## Day 1: Python and SQL from first principles

**Goal:** daily weather for two farms lands in Postgres, validated, and loading it
twice changes nothing.

**Read**
1. `seeds/locations.csv`: the two farms and their time zones.
2. `ingest/config.py`: every setting has a default; the environment wins.
3. `ingest/models.py`: the Pydantic contracts. Why is a null rain value rejected
   instead of being treated as 0?
4. `ingest/retry.py`: exponential backoff with jitter, as a decorator.
5. `ingest/weather.py`: fetch, land the raw JSON, parse, upsert. Find
   `latest_complete_day`, `write_json_atomic` and `UPSERT_SQL`.
6. `sql/ddl/raw_weather_daily.sql`: the natural key is the primary key.
7. `sql/queries/01` to `05`: window functions, LAG, ranking, gaps, dedupe.
8. `tests/test_weather.py`, `tests/test_weather_db.py`, `tests/fakes/open_meteo.py`.

**Run**
```bash
make up && make ingest && make queries
make psql    # then: select count(*) from raw.weather_daily;
```

**Break**
1. *Idempotency.* Run `make ingest` twice and count rows both times. Same count.
   Then compare `loaded_at` and `first_loaded_at` for one day: which one moved?
2. *Remove the upsert.* In `UPSERT_SQL`, delete the whole `ON CONFLICT ... DO UPDATE`
   part. Predict which test fails, then run `uv run pytest tests/test_weather_db.py`.
3. *Feed it bad data.* Open a landed file under `data/raw/weather/`, set one
   `precipitation_sum` value to `null`, then load just that file:
   `uv run python -c "from ingest.weather import upsert_file; print(upsert_file('<path>'))"`.
   One row fewer, and a "rejected" warning that says why.
4. *Flaky network.* `uv run python -m tests.fakes.open_meteo --port 8090 --fail-rate 0.5`,
   point `OPEN_METEO_ARCHIVE_URL` and `OPEN_METEO_FORECAST_URL` at it (see
   `.env.example`) and ingest. Watch the retries in the log.

**Explain**
- Why land the raw JSON before loading it, instead of loading straight from the API?
- What does `os.replace` guarantee that writing the file directly does not?
- Why does "yesterday" depend on the farm's time zone?
- What is the difference between `loaded_at` and `first_loaded_at`, and who needs each?

**Interview angle:** "Every load in this project is safe to repeat: natural keys,
upserts and deterministic file paths. That one property is what makes retries,
backfills and crash recovery boring."

---

## Day 2: Kafka, Redis and crash-safe streaming

**Goal:** twenty sensors stream through Kafka; a crash at any moment causes
reprocessing, never a lost or duplicated row.

**Read**
1. `docker-compose.yml`, the `kafka` service: KRaft, and the two listeners (who
   connects from where).
2. `ingest/sensor_producer.py`: idempotent producer, `acks=all`, keys by sensor id,
   the deterministic `event_id`, and the replay header on history.
3. `ingest/sensor_consumer.py`: start with the module docstring, then `handle()`.
   The order of the five steps is the whole lesson.
4. `sql/ddl/raw_sensor_readings.sql`: the primary key is the real guarantee; Redis
   is only a fast path.
5. `tests/test_sensor_consumer.py` with `tests/fakes/kafka.py`: how the loader is
   tested without a broker.

**Run**
```bash
make up-stream
make history    # 21 days of past readings
make consume    # Ctrl+C when it goes quiet
make group      # partitions, offsets and lag for agri-loader
```

**Break**
1. *Crash between write and commit.* Keep `make produce` running in one terminal.
   In another: `AGRI_CRASH_AFTER_WRITE=50 make consume`. It dies after 50 writes,
   before committing. Start `make consume` again. Then check for duplicates:
   `select sensor_id, ts, count(*) from raw.sensor_readings group by 1, 2 having count(*) > 1;`
   Expect no rows. Notice the restarted consumer may wait about 45 seconds first:
   the dead member keeps its partitions until its session times out.
2. *Poison message.* `make poison`, then `make dlq`. Read the headers: which check
   failed, and which partition and offset the message came from. The consumer did
   not stop.
3. *Rebalance.* Start two consumers (the second with `METRICS_PORT=8001 make consume`).
   `make group` shows the six partitions split between them. Stop one with Ctrl+C:
   the other takes all six almost at once. Now kill one with `kill -9`: the move
   takes about 45 seconds. Why the difference?
4. *Redis down.* `docker compose stop redis` while consuming. Warnings, but writes
   continue and the primary key still blocks duplicates. `docker compose start redis`.

**Explain**
- At-least-once delivery plus an idempotent write: why is that "effectively once"?
- Why commit the offset after the write and not before? What breaks the other way?
- Why key messages by sensor id?
- What does the DLQ need to carry so a bad message can be fixed and replayed later?

**Interview angle:** "I proved crash safety by crashing it: a lab hook kills the
consumer between the database write and the offset commit, and the row count stays
exact."

---

## Day 3: dbt and dimensional modelling

**Goal:** raw tables become staging views and marts with a stated grain, tests that
enforce it, and an SCD2 history of sensor placements.

**Read**
1. `transform/dbt_project.yml` and `transform/macros/generate_schema_name.sql`:
   folders map to schemas; the vars hold the thresholds.
2. `transform/models/staging/_sources.yml`: sources and freshness.
3. `stg_weather.sql`, `stg_sensor_readings.sql`: rename, cast, dedupe. Nothing else.
4. The intermediate models, then the marts (see the reverse trace above).
5. `transform/snapshots/sensors_snapshot.yml`: SCD2 with the check strategy.
6. `transform/tests/`: a custom generic test and a singular test.
7. `transform/models/marts/_marts.yml` and `_exposures.yml`: grain, docs, consumers.

**Run**
```bash
make dbt-build    # seeds, snapshot, models, every test
make signal
make dbt-docs     # lineage graph on http://localhost:8081
```

**Break**
1. *Move a sensor.* In `transform/seeds/sensors.csv`, move one sensor to another
   field. Run `uv run dbt seed && uv run dbt snapshot`, then query
   `snapshots.sensors_snapshot` for that sensor: two rows, with validity dates.
   Rebuild and see readings credited to the right field on each side of the move.
2. *Unknown is not false.* Delete today's weather rows in `raw.weather_daily` and
   rebuild: `irrigate` becomes NULL with "unknown: no weather for this day yet".
   What would go wrong if missing rain counted as 0 mm?
3. *A test that bites.* Insert a reading with moisture 150 into
   `raw.sensor_readings` and run `make dbt-build`. Which test fails, at which layer?
4. *Incremental.* Run `uv run dbt run -s fct_sensor_hourly` twice and read the
   SQL in `transform/target/run/`. Then add `--full-refresh` and compare.

**Explain**
- What is the grain of `agg_irrigation_signal`, and which test enforces it?
- Why a date spine, and why a RANGE window instead of ROWS?
- Why convert sensor hours to the farm's local date before grouping?
- Why does the incremental model look back 3 hours instead of 0?

**Interview angle:** "Every mart states its grain in one sentence and a test
enforces it, so a fan-out join fails the build before anyone reads a doubled number."

---

## Day 4: Airflow 3

**Goal:** a daily batch that owns one data interval per run, so a backfill run twice
gives identical results, and a report that runs when the data is ready.

**Read**
1. `dags/agri_daily.py`: the module docstring (the newspaper model), the timetable,
   then each task. Find where the asset is emitted.
2. `dags/irrigation_report.py`: scheduled on an asset, not on a time.
3. `scripts/airflow-env.sh`: Airflow in its own venv with its constraints file.
4. `tests/test_dags.py`: integrity tests that need no Airflow database.

**Run**
```bash
make down && make up        # on 8 GB, never Kafka and Airflow together
make airflow-install        # once
make airflow                # UI on http://localhost:8080
make backfill FROM=2026-09-21 TO=2026-10-04
```

**Break**
1. *Backfill twice.* Run the same backfill twice and fingerprint the result:
   `select md5(string_agg(t::text, '|' order by field_id, day)) from marts.agg_irrigation_signal t;`
   Same hash both times.
2. *The `@daily` trap.* Change the schedule to the string `"@daily"`. Run
   `make test-dags`. Which test fails, and what would the midnight run have fetched?
3. *Read the wall clock.* Add `date.today()` inside a task. `make test-dags` again.
4. *Report on bad data.* In `quality_check`, change `expected = len(load_locations())`
   to `expected = 3`, so the check fails. Trigger a run. Does `irrigation_report` run?

**Explain**
- What are `data_interval_start` and `data_interval_end` for the run of 1 October?
- Why is the asset emitted by `quality_check` and not by `dbt_build`?
- What does the dbt pool with one slot prevent during a backfill?

**Interview angle:** "Tasks read the data interval, never the clock, so a fortnight
of backfill is just fourteen normal runs, and running it twice changes nothing."

---

## Day 5: Docker, Terraform, GitHub Actions, AWS (for free)

**Goal:** one small non-root image, the AWS side as code with locked remote state,
and CI that blocks a broken pull request.

**Read**
1. `ingest/Dockerfile`: two stages, a pip-less venv, a non-root user.
2. `infra/versions.tf`, `providers.tf`, `variables.tf`: pinned versions, endpoints
   that switch between the emulator and real AWS.
3. `infra/bootstrap/main.tf`: the state bucket has to exist before the state can.
4. `infra/main.tf`, `infra/lambda.tf`, `infra/modules/s3_bucket/`: least-privilege
   IAM, a log group with retention, the EventBridge schedule.
5. `infra/lambda/weather_to_s3/handler.py` and `tests/test_lambda_handler.py` (moto).
6. `.github/workflows/ci.yml`: five jobs and what each proves.

**Run**
```bash
make image
make tf-validate
make tf-emulator-apply && make tf-emulator-run
make tf-emulator-destroy
```

**Break**
1. *Drift.* After applying, change the bucket by hand (for example its tags, with
   boto3 against the emulator). Run a plan with `-detailed-exitcode`: exit code 2
   means "the real world no longer matches the code".
2. *State lock.* Start two plans at once. The second one refuses to run. Why is
   that a feature?
3. *Image budget.* Add a large dependency and rebuild. Which CI step fails?
4. *Root.* Remove the `USER` line and rebuild. Which CI check catches it?

**Explain**
- Why does the bootstrap stack keep local state?
- What does `use_lockfile` replace, and why was the old way retired?
- Why does the Lambda role get `s3:PutObject` on one prefix and nothing else?

**Interview angle:** "The whole AWS side runs for free against a local emulator with
the same Terraform; switching to a real account is a backend file and a variable."

---

## Day 6: RAG, tool use, agents and evals

**Goal:** an assistant that answers from agronomy documents with citations, asks a
validated tool for field data, refuses what it cannot support, and is scored on
every change.

**Read**
1. `ai/sources.json` and `ai/fetch_docs.py`: the documents and where they come from.
2. `ai/ingest_docs.py`: the docstring first. Extract, chunk, embed, store. Read
   `_superscript` and the table handling: both came from real failures.
3. `sql/ddl/doc_chunks.sql`: pgvector and the HNSW index.
4. `ai/rag.py`: retrieval is one SQL query.
5. `ai/tools.py`: the only way the model touches farm data. Read `summarize` and its
   docstring: it came from reading the eval answers, not the eval scores.
6. `ai/llm.py`: one interface over Ollama and Claude.
7. `ai/ask.py`: the system prompt, tool gating and the agent loop.
8. `ai/evals/golden.json` and `ai/evals/run_evals.py`, then `ai/planted/field_office_note.md`.

**Run**
```bash
make docs-fetch && make docs-ingest
make ask Q="What is the mid-season crop coefficient for sweet peppers?"
make ask Q="Should field F-03 be irrigated this week?"
make evals
```

**Break**
1. *Chunk size.* `uv run python -m ai.ingest_docs --chunk-tokens 200 --overlap-tokens 20`,
   then `make evals`. Compare with the saved baseline in `ai/evals/baseline.json`.
   Put it back with `make docs-ingest`.
2. *The judge.* Replace `JUDGE_PROMPT` with a bare "Reply YES or NO". Run `make evals`
   and read the calibration line.
3. *Injection.* Read the planted note, then ask the i1 question. Remove the line in
   `SYSTEM_PROMPT` about instructions inside sources and ask again.
4. *Injection through a tool argument.* `make ask Q="Check field F-01'; DROP TABLE marts.dim_field;--"`.
   Where exactly is it stopped?
5. *Take the summary away.* In `get_field_conditions`, pass `summary=""` instead of
   `summarize(days)`, then `make ask Q="Should field F-03 be irrigated this week?"`.
   Compare the answer with the rows the tool returned. Then put it back.

**Explain**
- Why embed the question with `search_query:` and the chunks with `search_document:`?
- Why does a table become one chunk per row, with the column names repeated?
- Why is the tool offered only for questions that mention a field?
- What does calibrating the judge protect against?
- The first baseline scored tool use 3/3 while one answer was backwards. What was that
  metric really measuring, and what measures the answer?
- In `ai/evals/baseline.json`, d7 is the one answer the judge rejected. Read it next to
  its sources: was the judge right?

**Interview angle:** "The eval harness caught its own judge: asked for a bare YES or
NO, the small model failed everything, so the judge now has to show its evidence and
pass a known-good and known-bad check before its numbers count. Then reading the
answers caught a metric: tool use was 3/3 while the model told me a dry field needed no
water, so the decision moved into code and tool answers are now judged too."

---

## Day 7: Kubernetes, observability and SLOs

**Goal:** the consumer runs as two replicas with probes and limits; metrics, alerts
and a dashboard show its health; two SLOs say whether the platform is doing its job.

**Read**
1. `ingest/observability.py`: RED metrics, liveness and readiness on one port.
2. `k8s/deployment.yaml` then `k8s/cronjob-weather.yaml`, `k8s/configmap.yaml`,
   `k8s/kustomization.yaml`: every comment explains one decision.
3. `monitoring/prometheus.yml`, `monitoring/alerts.yml`, `monitoring/alerts_test.yml`.
4. `monitoring/grafana/`: provisioning means no clicking.
5. `sql/slo/01_weather_freshness.sql`, `sql/slo/02_sensor_latency.sql`.
6. `scripts/verify_mac.sh`: the whole deployment checked end to end.

**Run**
```bash
make up-stream && make up-monitoring
make consume                 # in its own terminal
make produce                 # in another
make metrics                 # health, readiness, counters
make slo
make alerts-check && make k8s-validate
make verify-mac              # kind: build, load, deploy, check
```

**Break**
1. *Fire an alert.* `make poison`, then open http://localhost:9090/alerts. How long
   until `SensorMessagesInDLQ` fires, and why that long?
2. *Lag.* Stop the consumer and keep `make produce` running. Watch lag climb in
   Grafana (http://localhost:3000). When does `ConsumerLagGrowing` go from pending
   to firing?
3. *Rollout without downtime.* `kubectl -n agri rollout restart deployment/sensor-consumer`
   and `kubectl -n agri get pods -w`. With `maxUnavailable: 0`, when does an old pod
   stop?
4. *Out of memory.* Set the memory limit to `40Mi`, apply, and read
   `kubectl -n agri describe pod`. What does OOMKilled look like?
5. *A typo Kubernetes would ignore.* Rename `replicas` to `replica` and run
   `make k8s-validate`. Strict validation catches what `kubectl apply` might not.
6. *Burn the error budget.* With `make produce` running, stop the consumer for two
   minutes, restart it and run `make slo`. How much of the month's budget went?

**Explain**
- Liveness and readiness: which one restarts the pod, and why must they differ?
- Why alert on symptoms (bad data, lag) and not on CPU?
- Why `first_loaded_at` for freshness, and why leave replayed history out of latency?
- 99.5% of readings within 60 seconds over 30 days: what is the error budget?

**Interview angle:** "Alerts have unit tests: promtool feeds in made-up series and
proves each alert fires when it should and stays quiet when it should."

---

## Day 8: MCP

**Goal:** the same field data, served to any MCP client through a read-only server.

**Read**
1. `sql/roles/mcp_reader.sql`: least privilege enforced by Postgres.
2. `ai/mcp_server.py`: tools, a resource and a prompt around the Day 6 functions.
3. `tests/test_mcp_server.py`: a real MCP client, in-process and over stdio.
4. `.mcp.json`: how Claude Code finds the server in this repo.

**Run**
```bash
make ddl            # creates the mcp_reader role
make mcp-check
make mcp-inspect    # the MCP Inspector in a browser (needs Node)
```
Then open the repo in Claude Code, approve the `agri-data` server, and ask
"Which fields need irrigation today, and why?"

**Break**
1. *Try to write.* `psql postgresql://mcp_reader:mcp_reader@localhost:5432/agri`,
   then `delete from marts.dim_field;`. Then `set default_transaction_read_only = off;`
   and try again. Two different errors: which lock stopped each attempt?
2. *Try to read raw.* `select * from raw.weather_daily limit 1;` as mcp_reader.
3. *Rebuild the marts.* `make dbt-build`, then query a mart as mcp_reader. Why does
   it still work after dbt dropped and recreated the table?
4. *Leak check.* In the Inspector, call `get_field_conditions` with `F-42` and with
   `DROP TABLE`. Compare the two error messages with what the server log shows.

**Explain**
- Tools, resources and prompts: who decides when each is used?
- Why are there three layers of defence if the first one already validates input?
- Why must an MCP server never print to stdout?

**Interview angle:** "The model never gets SQL. It gets two task-shaped tools, and the
database login behind them cannot write even if every check above it failed."

---

## Mock interview

Answer each in two minutes, out loud, then check against the code.

1. Walk me through what happens between a sensor reading and the irrigation decision.
2. Your consumer crashes after writing a row but before committing the offset. What happens?
3. How would you change this design for 10,000 farms? (README has my sketch.)
4. A backfill of last month runs while the daily job also runs. What could go wrong, and what prevents it here?
5. How do you know the RAG assistant got better after a change, and not just different?
6. What is the difference between a liveness probe and a readiness probe? Give an example where mixing them up causes an outage.
7. Your freshness SLO is 99% of days. Last month you missed two days. What do you do?
8. How do you stop a prompt injection in a retrieved document from reaching the database?
9. Why dbt snapshots for sensor placements, rather than just updating the seed?
10. If you had one more week, what would you build next, and why that?
