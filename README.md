# Neighboar

![Neighboar](./images/Neighboar.png)

Neighboar helps first-time buyers of an HDB resale flat in Singapore decide where to look and what to pay. It answers questions from every resale sale registered with HDB since January 2017: which towns and blocks fit a budget, what a block is like, whether an asking price is fair, and how prices have moved. Two people buying together can share one shortlist.

It is the INF2003 database project of Team 65. Every feature is built twice, once on PostgreSQL and once on MongoDB, over the same data, and the two must give the same answers.

## Why

**For buyers.** A resale flat is the largest purchase most Singapore households make, and HDB publishes every resale sale, but as a raw table of 240,000 rows. A couple with a budget and a parent's block to live near cannot easily turn that into "which blocks fit, and is this asking price fair?". Neighboar does that, and it is honest about the data: these are past sales, not flats for sale today, and every figure says how many sales it rests on.

**For the course.** The assignment is to build a real application on an existing relational database and an existing non-relational one, not a custom engine, and to compare them. The same domain is modelled both ways: a normalised PostgreSQL schema with constraints, triggers and a materialised view, and a MongoDB model that embeds what a page reads together. The two implementations sit behind one set of repository interfaces, so every page can be answered by either database, equivalence tests prove the answers match, and a benchmark compares them on the same data under documented conditions.

## What it does

| Page | What a buyer does there |
| --- | --- |
| Find a home | Three questions (flat type, budget, optionally a block to live near), then the towns and blocks that fit, with a map |
| Block profile | When it was built, lease left (now or in a chosen year), recent prices against the town, latest sales, nearest hawker centres |
| Check a price | Compare an asking price per square metre with sales in the same block, street or town over 24 months |
| Town trends | Monthly typical price and sales for a town and flat type, with a 12-month line |
| Shortlist and compare | Save blocks with notes (up to 50), compare 2 to 4 side by side |
| Household | Invite a partner and share one shortlist; accept merges both lists in one transaction |
| Admin | Record or delete a sale; a "simulate failure" option shows the rollback |
| Demo mode | For presenters: switch each page between PostgreSQL and MongoDB, see every query and its time, and open "Behind the scenes" for health, query plans and benchmark results |

The [user manual](docs/user_manual.md) walks through every page with screenshots.

## Stack

| Layer | Choice |
| --- | --- |
| Web app | Python 3.12, FastAPI, Jinja2 server-rendered pages, vanilla JavaScript and CSS; Chart.js and Leaflet vendored, OpenStreetMap tiles |
| Relational database | PostgreSQL 18.6 (psycopg 3) |
| Document database | MongoDB 8.3.11 as a single-node replica set, for transactions (PyMongo) |
| Runtime | Docker Compose; nothing else is needed on the host |
| Data pipeline | Python with pandas and parquet (`etl/`) |
| Tests | pytest: unit, integration against the running stack, equivalence between the databases, negative tests of every integrity rule |
| Benchmark | `bench/`: timed cases, throughput, scaling and index on/off, with charts |
| CI | GitHub Actions: lint (ruff), the whole stack on the sample data, every test |

Both database images are pinned to exact versions, so every machine and the benchmark run the same releases. The app connects with least-privilege accounts: buyers' pages use a read-write role without schema rights, the admin routes a separate role, and the benchmark's reads a select-only role.

## Data

Three public datasets from [data.gov.sg](https://data.gov.sg), pinned as one snapshot retrieved on 26 September 2026 (`snapshot-2026-09`). `etl/manifest.json` holds each file's SHA-256, and the loader refuses any file that does not match.

| File | Publisher | Rows | Covers |
| --- | --- | ---: | --- |
| [Resale flat prices based on registration date from Jan 2017 onwards](https://data.gov.sg/datasets/d_8b84c4ee58e3cfc0ece0d773c8ca6abc/view) | Housing & Development Board | 241,357 | Every resale sale registered from Jan 2017 to Sep 2026 |
| [HDB Property Information](https://data.gov.sg/datasets/d_17f5382f26140b1fdae0ba2ef6239d2f/view) | Housing & Development Board | 13,357 | Every HDB block: address, year completed, flats by type |
| [Hawker Centres (GeoJSON)](https://data.gov.sg/datasets/d_4a086da0a5553be1d89383cd90d07ecd/view) | National Environment Agency | 129 | Hawker centres with their locations and status |

Also used:

- **Block coordinates.** Every residential or resold block (10,797) was geocoded once through OneMap (`etl/geocode.py`); the result is committed in `data/cache/block_coordinates.csv`, so the app never calls an external service for data.
- **Synthetic buyers.** Every load writes 200 buyers with about 1,000 saved blocks, 10 households and 5 open invitations to both databases from a fixed seed (`etl/synth_buyers.py`). Two of them are the documented demo accounts.
- **A committed sample** of about 6,000 sales in three towns (`data/sample/`), which CI and quick trials load instead of the full snapshot.

The three source files are not in the repository. data.gov.sg updates the resale dataset monthly, so a fresh download will not match the pinned checksums; the snapshot files come with the submission (see the manual, step 5). `docker compose run --rm etl python -m etl.download --check-live` reports how far the live datasets have moved since.

Cleaning (`etl/clean.py`) maps HDB's property town codes to town names, normalises block and street keys (upper case, trimmed, single spaces) so the two HDB files join, and keeps every sale: all 241,357 load, with one stub block for a sold address missing from the property dataset. `docs/import_report.md` records every count, and `docs/data_dictionary.md` every column.

## How the two databases model it

- **PostgreSQL** (`db/postgres/`): 13 tables in third normal form: towns, blocks, flat types and models, flats by type per block, sales, hawker centres, buyers and their saved blocks, and four for households. Foreign keys, checks and triggers enforce the rules (a household has at most two members, a shared shortlist at most 50 blocks, a member has no personal shortlist). A materialised view holds the monthly town figures.
- **MongoDB** (`db/mongo/`): `building_profiles` embeds a block's details, latest sales and statistics so a block page is one read; `resale_transactions` keeps the full history; `buyers` embeds saved blocks; `households` embeds members and the shared shortlist; `hawker_centres` and block locations use 2dsphere indexes. Collection validators and unique indexes enforce the same rules; multi-document changes run in transactions.

Diagrams are in `docs/diagrams/` (ERD, document model, architecture). `docs/integrity_matrix.md` maps each rule to how both databases enforce it and the test that proves it.

## Run it

You need Docker Desktop (Windows, macOS) or Docker Engine with the Compose plugin (Linux), Git, about 6 GB of free disk and 6 GB of memory for Docker. The [user manual](docs/user_manual.md) has every step in detail, with troubleshooting and platform notes.

```bash
git clone https://github.com/C-O-L0/Neighboar.git && cd Neighboar
cp .env.example .env                      # replace every change-me value (manual, step 3)
docker compose up -d --build --wait web   # builds and starts both databases and the app
```

Load the data, either the full snapshot (put the three files in `data/raw/` first) or the committed sample:

```bash
docker compose run --rm etl python -m etl.download   # checks the three files against the manifest
docker compose run --rm etl                          # full load, about a minute
docker compose run --rm etl python -m etl.pipeline --sample   # or: the sample, no files needed
```

On Linux, add `--user "$(id -u):$(id -g)"` after `run --rm` in each `etl` command, so the load can write `data/interim/` (manual, section 14).

Open http://localhost:8000. Sign in as `demo@neighboar.local` (buyer) or `admin@neighboar.local` (administrator) with the passwords you set as `DEMO_BUYER_PASSWORD` and `DEMO_ADMIN_PASSWORD` in `.env`, or register an account. Turn on Demo mode at the bottom right to switch databases.

Run every test against the running stack, straight after a load:

```bash
set -a; . ./.env; set +a
docker compose run --rm --no-deps -e NEIGHBOAR_URL=http://web:8000 -e PG_APP_RO_PASSWORD -e PG_OWNER_PASSWORD -e MONGO_ETL_PASSWORD -e DEMO_BUYER_PASSWORD -e DEMO_ADMIN_PASSWORD web python -m pytest -q -m "integration or not integration"
```

### Services

```text
postgres  PostgreSQL 18.6, named volume pgdata
mongodb   MongoDB 8.3.11, single-node replica set, named volume mongodata
migrate   one-off: replica set, roles and users, schema migrations, validators and indexes, then exits
web       the app at http://localhost:8000 (bound to localhost only), after migrate succeeds
etl       on demand (profile "tools"): checks the snapshot, cleans it, loads both databases
bench     on demand (profile "tools"): the benchmark harness, run through bench/run.sh
dbtools   on demand (profile "tools"): pg_dump, pg_restore, mongodump, mongorestore for the backup scripts
```

`docker compose down` keeps the data; only `docker compose down -v` deletes both databases.

### Backup and restore

```bash
sh scripts/backup.sh --stop-web                # writes backups/<UTC timestamp>/
sh scripts/restore.sh backups/<UTC timestamp>
```

A backup holds `postgres.dump`, `mongo.archive.gz` and a manifest with checksums of the dumps and the schema files and a fingerprint of both databases. `restore.sh` checks the checksums and the schema files before changing anything, asks you to type `yes`, restores both databases and compares the fingerprint, and leaves `web` stopped if anything differs. `sh scripts/check_backup_roundtrip.sh` runs the whole round trip as a self-test. `backups/` holds full copies including password hashes: it is git-ignored and must not be shared or zipped.

## Benchmark

`bench/` times the same questions in both databases on the full data, warm cache, 3 parameter sets of 50 runs each, after checking that both give the same results. It also measures throughput at 1, 4 and 8 clients, latency at 25, 50 and 100 per cent of the sales, and each index on and off with its query plans. `bench/README.md` is the procedure; `python -m bench.report` writes the results file from the run's own CSVs.

The latest run, `bench/results/2026-11-rerun/` (one Linux laptop on mains power), covers 20 cases including the new pages' queries; its [RESULTS.md](bench/results/2026-11-rerun/RESULTS.md) has every table and chart. In short, MongoDB is lower for the block page (one embedded document), blocks near a block (`$geoNear` against Haversine over every block), filtered and price-sorted search (helped by an extra compound index PostgreSQL does not have), trends and rankings. PostgreSQL is lower for towns and blocks that fit, price check comparables, the exact median, the precomputed town figures and hawker centres per town. Household accept and leave, unfiltered search and search by flat type and month are within about 20 per cent of each other. The earlier run on Windows is in `bench/results/2026-11-full/`; compare numbers only within one run.

## Repository

```text
app/        FastAPI app: routers, services, templates, static files; repos/pg and repos/mongo implement the same interfaces
db/         PostgreSQL migrations, MongoDB validators and indexes, migrate step, backup tools image
etl/        snapshot check, cleaning, geocoding cache, loaders for both databases, synthetic buyers, import report
tests/      unit, integration, equivalence and negative tests
bench/      benchmark cases, harness, charts, report generator and results
docs/       user manual, conventions, data dictionary, import report, integrity matrix, diagrams, build plan, references
scripts/    backup, restore and their round-trip check
data/       sample data and the geocoding cache (the full snapshot goes in data/raw/, not committed)
```

Further reading: [`docs/conventions.md`](docs/conventions.md) (how a feature is built in both databases: repositories, DTOs, the database switch, timing, equivalence tests), [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) (the original design and plan), [`docs/references.md`](docs/references.md) (libraries and sources).

## Working on it

- One branch and one pull request per task into `main`; a teammate reviews before merging, and CI must pass.
- Change schemas and seed data only through committed migrations and the ETL, never by hand in a running database, so everyone can rebuild the same state.
- Keep `.env`, database volumes, the snapshot files and backups out of commits.
- A new feature needs both databases, an equivalence test, and negative tests for any new integrity rule (`docs/conventions.md`).
