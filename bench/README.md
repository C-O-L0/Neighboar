# Benchmark procedure (task 4.2)

The formal full-data run, followed in this order. Every command is for Git Bash, run from the repository root, with the stack's `.env` in place. Do not start anything else that uses the databases while a step runs. Timings are warm cache only, on one machine.

Each run goes to its own folder, `bench/results/<run>/`, in three sub-folders: `f100/` (all of the full data), `f50/` and `f25/`. A label with one slash, such as `$RUN/f100`, makes that folder tree; the invocations of step 3 all append into the one folder. Two runs are committed: `2026-11-full/` (the first, Docker Desktop on Windows) and `2026-11-rerun/` (the latest, with the cases for the new pages, on a Linux laptop). Compare numbers only within one run.

For a new run, choose a new name, set it once in the shell and follow every step below; the commands use `$RUN`. Never time into the folder of an earlier run.

```sh
RUN=2026-12-rerun   # a new folder name under bench/results/
```

Every `bench/run.sh` call collects the host facts (commit, image ids, Docker memory, the cache size the running MongoDB was started with), refuses to time if `bench.verify` or the database counts fail, and prints a `done:` line on success. A refusal exits with status 2 and changes nothing.

## 0. Before you start

```sh
git status --short          # clean apart from bench/results; the run is recorded with the commit, plus -dirty if not
docker compose up -d --build --wait web
docker compose build bench etl
docker compose stop web
```

On a laptop, plug it in and set the performance power profile first (`powerprofilesctl set performance` on Linux, the Best performance mode on Windows), and leave the machine alone until the run ends. `bench/run.sh` refuses to time on battery and records the power source and profile of every invocation in `env.json`.

Stop `web` before timing: its healthcheck queries both databases every 10 s, which would land inside timed calls. It stays stopped until the end of step 6.

## 1. Cache sizes for the run and recreated database containers

Edit `.env` (it is not committed) so these two lines read exactly:

```
PG_SHARED_BUFFERS=1GB
MONGO_CACHE_GB=1
```

Recreate both database containers (`web` stays stopped). Volumes are kept.

```sh
docker compose up -d --force-recreate --wait postgres mongodb
```

## 2. Reload both databases from the pinned snapshot

```sh
docker compose run --rm etl
```

On Linux, add `--user "$(id -u):$(id -g)"` after `run --rm` in this and every other `etl` command, so the load can write `data/interim/` (user manual, section 14). The load ends with the settle (`refresh_town_stats()`) and the database counts check. Both databases must show 241,357 sales (`docs/import_report.md`). `bench.run` repeats the check before it times anything.

## 3. Full data, into `$RUN/f100`

Run these four in this order, one at a time:

```sh
sh bench/run.sh --label $RUN/f100
sh bench/run.sh --label $RUN/f100 --include-writes
sh bench/run.sh --label $RUN/f100 --throughput
sh bench/run.sh explain --label $RUN/f100
```

The first runs every read case (3 parameter sets of 50 runs, 5 warm-up calls each) and `bench.verify`. A second read run into the same label refuses unless you add `--replace`. The second command runs only the write cases (`stats_rebuild`, `admin_record_delete`, `household_accept_leave`), the third only the throughput levels (1, 4 and 8 worker processes, about 7 minutes), and the last the index on and off comparison.

Check afterwards that `bench/results/$RUN/f100/env.json` shows 1 GB in both cache fields, 241,357 sales in both databases, `"fraction"` of 1 and four entries under `invocations`.

## 4. Scaling: 25% then 50% of the sales

Load a fraction. Cleaning still sees the whole snapshot; only the sales are filtered (`txn_id % 4 == 0`, then `% 2 == 0`). `data/interim` is not written.

```sh
docker compose run --rm etl python -m etl.pipeline --fraction 0.25
sh bench/run.sh --label $RUN/f25 --expect-fraction 0.25 --cases search_filtered,trends_series,rankings_year,building_page

docker compose run --rm etl python -m etl.pipeline --fraction 0.5
sh bench/run.sh --label $RUN/f50 --expect-fraction 0.5 --cases search_filtered,trends_series,rankings_year,building_page

docker compose run --rm etl
```

The last command is the full reload. `--expect-fraction` makes the run refuse if the loaded data is not the fraction named, whatever the label says.

## 5. Merge the scaling results

```sh
export HOST_UID=$(id -u) HOST_GID=$(id -g)
docker compose run --rm bench python -m bench.scaling bench/results/$RUN
```

This reads the fraction from each folder's `env.json` (not from the folder name) and writes `bench/results/$RUN/scaling.csv`. It needs exactly three different fractions and replaces an earlier `scaling.csv`. The `export` makes the container write as you, so the files are yours on Linux as well as Docker Desktop (`bench/run.sh` does the same for you).

## 6. Restore the defaults and the full snapshot

Edit `.env` back to the defaults:

```
PG_SHARED_BUFFERS=512MB
MONGO_CACHE_GB=0.5
```

```sh
docker compose up -d --force-recreate --wait postgres mongodb
docker compose run --rm etl
docker compose up -d --wait web
```

Afterwards the local databases hold the full snapshot with every index and the default cache sizes, and `web` runs again.

## Charts

The charts are built from the committed CSVs alone (`f100/summary.csv`, `f100/throughput.csv`, `f100/index_on_off.csv` and `scaling.csv`), so they can be regenerated at any time, without the databases:

```sh
export HOST_UID=$(id -u) HOST_GID=$(id -g)
docker compose run --rm bench python -m bench.charts bench/results/$RUN
```

This writes `latency_by_case`, `throughput`, `scaling` and `index_on_off`, each as a PNG (300 dpi, 16 cm wide) and an SVG, into the same folder. Where a chart needs one number per case and database, the median is the median of the three parameter sets' medians and the p95 whisker is the largest of the three p95 values. The refresh half of `admin_record_delete` is not charted; `household_accept_leave` gets one bar for accept and one for leave. `scaling.png` shows the cases that were run at all three fractions.

## RESULTS.md

`RESULTS.md` holds every table of a run and a reading of each, generated from the same files as the charts and the `env.json` of each fraction; no number in it is typed. Say which operating system the run used (`env.json` does not record it):

```sh
docker compose run --rm bench python -m bench.report bench/results/$RUN --host linux   # or --host windows
```

On the committed `2026-11-full` run it reproduces that folder's `RESULTS.md` exactly (`tests/unit/test_bench_report.py`).

## Notes

- A label may have at most one `/`, and each part starts with a letter or digit (`2026-11-rerun/f100`, not `../x`).
- If a run is interrupted, start the next `bench/run.sh` call as usual: it recreates any missing MongoDB index first, and PostgreSQL index changes in the index comparison are rolled back.
- Compare PostgreSQL only with itself and MongoDB only with itself in the index table. MongoDB also has the `{town, flat_type, txn_month}` index, which PostgreSQL has no equivalent of; it favours MongoDB in `search_filtered`.
- The two read identities differ in what they could do (PostgreSQL `app_ro` is `SELECT` only; MongoDB `app` can also write `buyers` and `counters`, which the harness never does).
