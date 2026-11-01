"""Write RESULTS.md for one benchmark run from its committed CSVs and env.json files (task 4.2, T17).

    python -m bench.report bench/results/2026-11-rerun --host linux

Every number comes from <folder>/f100/summary.csv, f100/throughput.csv, f100/index_on_off.csv, scaling.csv and the
env.json of each fraction; none is typed. --host names the machine the run used (the env.json files do not record
the host operating system). The chart value of a case and database is the median of its three parameter sets'
medians and the whisker the largest p95, the same rule as bench.charts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from bench.charts import LOG_RANGE, _median, latency_data, read_csv

DB_NAMES = {"pg": "PostgreSQL", "mongo": "MongoDB"}
WITHIN = 1.10  # chart values closer than this ratio read as "within 10 per cent"
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
HOSTS = {
    "windows": "One machine, Docker Desktop on Windows. The results say nothing about other hardware or a Linux host.",
    "linux": "One machine, Docker Engine on Linux. The results say nothing about other hardware or a Windows host.",
}
LATENCY_HEAD = (
    "| Case | Parameter set | PG median ms | PG p95 ms | PG server ms | PG rows | Mongo median ms | Mongo p95 ms "
    "| Mongo server ms | Mongo rows |\n|---|---|---|---|---|---|---|---|---|---|"
)

# Section 1, in order. A section is printed when any of its cases is in the run; `note` follows its Reading line.
SECTIONS = [
    {
        "title": "Live queries (search, trends, rankings): same question, same result in both databases",
        "cases": ["search_filtered", "search_type_month", "search_price_sort", "search_unfiltered", "trends_series",
                  "rankings_year"],
        "note": "`search_filtered` runs against the extra MongoDB `{town, flat_type, txn_month}` index, which favours "
                "MongoDB; the PostgreSQL side has no equivalent.",
    },
    {
        "title": "Live queries of the new screens (explore, price check): same question, same result in both databases",
        "cases": ["towns_fit", "blocks_fit", "comparables"],
        "note": "`towns_fit` reads every sale of the flat type in the 12 months to the data month and groups by town. "
                "`blocks_fit` returns one page of 20 blocks with the total, so its rows column is 1 in both databases; "
                "its last set also filters by distance from a block. `comparables` is one level of the price check "
                "(block, street or town) over 24 months.",
    },
    {
        "title": "Embedded document against join: `building_page`",
        "cases": ["building_page"],
        "note": "PostgreSQL joins the live tables; MongoDB reads one `building_profiles` document.",
    },
    {
        "title": "Embedded document against `$lookup`: `building_recent_lookup` (MongoDB only)",
        "cases": ["building_recent_lookup"],
    },
    {
        "title": "Precomputed summaries: `town_stats_read` and `stats_rebuild` (not result-equivalent)",
        "cases": ["town_stats_read", "stats_rebuild"],
        "note": "Grain: these are not result-equivalent. `town_stats_read` in PostgreSQL reads the month rows of one "
                "materialised view for a town and flat type (about 117 town-month rows); in MongoDB it reads "
                "`stats.by_flat_type` of every block in the town, so its row count is the number of blocks in the "
                "town and varies by town (see the rows columns). `stats_rebuild` in PostgreSQL is `REFRESH ... "
                "CONCURRENTLY` of one materialised view; in MongoDB it is a reset and `$merge` over about 13k "
                "building profiles (13,358 buildings in `docs/import_report.md`). The two rebuilds do different "
                "amounts of work, so the "
                "ratio is not a like-for-like speed comparison.",
    },
    {
        "title": "Geo",
        "cases": ["food_town", "blocks_near"],
        "titled": True,
        "note": "`blocks_near` in PostgreSQL computes the Haversine distance to every located block (there is no "
                "spatial index); MongoDB uses `$geoNear` on a 2dsphere index.",
        "note_if": "blocks_near",
    },
    {
        "title": "Median: `median_exact` and `median_approx` (MongoDB only for the approximate one)",
        "cases": ["median_exact", "median_approx"],
        "note": "`median_exact` returns the lower median, an observed price, from both databases. `median_approx` is "
                "MongoDB only and has no PostgreSQL pair.",
    },
    {"title": "Write: `admin_record_delete`", "cases": ["admin_record_delete"], "kind": "admin"},
    {"title": "Write: `household_accept_leave`", "cases": ["household_accept_leave"], "kind": "household"},
]  # fmt: skip


# ------------------------------------------------------------------------------------------------- formatting


def ms(value: float) -> str:
    return f"{value:,.4f}"


def count(value) -> str:
    return f"{int(float(value)):,}"


def server(row: dict) -> str:
    if row["server_ms"] == "":
        return "-"
    value = float(row["server_ms"])
    return ms(value) if row["db"] == "pg" else f"{round(value):,}"  # MongoDB reports whole milliseconds


def words(n: int) -> str:
    return NUMBER_WORDS.get(n, str(n))


def join_and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ----------------------------------------------------------------------------------------------- latencies


class Run:
    def __init__(self, folder: Path):
        self.folder = folder
        self.rows = read_csv(folder / "f100" / "summary.csv")
        self.env = json.loads((folder / "f100" / "env.json").read_text(encoding="utf-8"))
        self.cases = list(dict.fromkeys(r["case"] for r in self.rows))

    def sets(self, case: str) -> list[str]:
        return list(dict.fromkeys(r["param_set"] for r in self.rows if r["case"] == case))

    def row(self, case: str, db: str, param_set: str, part: str) -> dict | None:
        for r in self.rows:
            if (r["case"], r["db"], r["param_set"], r["part"]) == (case, db, param_set, part):
                return r
        return None

    def chart(self, case: str, db: str, part: str) -> tuple[float, float] | None:
        found = [r for r in self.rows if (r["case"], r["db"], r["part"]) == (case, db, part)]
        if not found:
            return None
        return _median([float(r["median_ms"]) for r in found]), max(float(r["p95_ms"]) for r in found)

    def main_part(self, case: str) -> str:
        return next(r["part"] for r in self.rows if r["case"] == case)


def latency_rows(run: Run, case: str) -> list[str]:
    part = run.main_part(case)
    out = []
    for ps in run.sets(case):
        cells = [f"`{case}`", ps]
        for db in ("pg", "mongo"):
            r = run.row(case, db, ps, part)
            cells += ["-"] * 4 if r is None else [ms(float(r["median_ms"])), ms(float(r["p95_ms"])), server(r),
                                                  count(r["rows"])]  # fmt: skip
        out.append("| " + " | ".join(cells) + " |")
    cells = [f"`{case}`", "**chart value**"]
    for db in ("pg", "mongo"):
        cv = run.chart(case, db, part)
        cells += ["-"] * 4 if cv is None else [f"**{ms(cv[0])}**", f"**{ms(cv[1])}**", "-", "-"]
    out.append("| " + " | ".join(cells) + " |")
    return out


def verdict(run: Run, case: str, part: str | None = None) -> str:
    part = part or run.main_part(case)
    pg, mongo = run.chart(case, "pg", part), run.chart(case, "mongo", part)
    if pg is None or mongo is None:
        db, cv = ("pg", pg) if mongo is None else ("mongo", mongo)
        return f"`{case}`: {DB_NAMES[db]} only, chart value {ms(cv[0])} ms (p95 up to {ms(cv[1])} ms)"
    a, b = pg[0], mongo[0]
    ratio = max(a, b) / min(a, b)
    both = f"(PostgreSQL {ms(a)} ms, MongoDB {ms(b)} ms)"
    head = f"within 10 per cent {both}" if ratio < WITHIN else (
        f"{'PostgreSQL' if a < b else 'MongoDB'} lower, {ratio:.1f} times {both}"
    )  # fmt: skip
    lower = []
    for ps in run.sets(case):
        p, m = run.row(case, "pg", ps, part), run.row(case, "mongo", ps, part)
        lower.append("PostgreSQL" if float(p["median_ms"]) < float(m["median_ms"]) else "MongoDB")
    if len(set(lower)) == 1:
        tail = f"{lower[0]} is lower in all {len(lower)} parameter sets"
    else:
        tail = f"the lower database differs between parameter sets ({', '.join(lower)} in table order)"
    return f"`{case}`: {head}; {tail}"


def admin_section(run: Run) -> list[str]:
    case = "admin_record_delete"
    pg, mongo = run.chart(case, "pg", "write"), run.chart(case, "mongo", "write")
    return [
        LATENCY_HEAD,
        *latency_rows(run, case),
        "",
        f"Reading: `{case}`: write half only, PostgreSQL chart value {ms(pg[0])} ms and MongoDB {ms(mongo[0])} ms. "
        "The two write halves do different work (MongoDB's includes the block stats recomputation, PostgreSQL's does "
        "not), so no verdict on which is lower is drawn from them.",
        "",
        "The table is the write half only; the PostgreSQL refresh that follows each delete is in section 5.",
    ]


def household_section(run: Run) -> list[str]:
    case = "household_accept_leave"
    head = (
        "| Parameter set | PG accept median ms | PG accept p95 ms | PG leave median ms | PG leave p95 ms "
        "| Mongo accept median ms | Mongo accept p95 ms | Mongo leave median ms | Mongo leave p95 ms |\n"
        "|---|---|---|---|---|---|---|---|---|"
    )
    out = [head]
    for ps in run.sets(case):
        cells = [ps]
        for db in ("pg", "mongo"):
            for part in ("accept", "leave"):
                r = run.row(case, db, ps, part)
                cells += [ms(float(r["median_ms"])), ms(float(r["p95_ms"]))]
        out.append("| " + " | ".join(cells) + " |")
    cells = ["**chart value**"]
    for db in ("pg", "mongo"):
        for part in ("accept", "leave"):
            cv = run.chart(case, db, part)
            cells += [f"**{ms(cv[0])}**", f"**{ms(cv[1])}**"]
    out.append("| " + " | ".join(cells) + " |")
    readings = [verdict(run, case, part).replace(f"`{case}`", f"`{case}` {part}", 1) for part in ("accept", "leave")]
    out += [
        "",
        f"Reading: {'; '.join(readings)}.",
        "",
        "Each timed run sends an invitation (untimed), then accepts it with the partner's blocks merged and leaves, "
        "each one transaction timed as its own part, and then puts both synthetic buyers back as they were. Both "
        "databases do the same logical work: PostgreSQL locks the two buyer rows and writes the household, member and "
        "shared rows; MongoDB writes one household document and both buyer documents in a transaction. Client timing "
        "only.",
    ]
    return out


def section_one(run: Run) -> list[str]:
    values = [v[0] for _, v in latency_data(run.rows)[1].items()]
    axis = (
        "The chart uses a log axis because the range of values exceeds 100 times"
        if max(values) / min(values) > LOG_RANGE
        else "The chart uses a linear axis because the range of values is within 100 times"
    )
    charted = "The refresh half of `admin_record_delete` is not charted (section 5)."
    if "household_accept_leave" in run.cases:
        charted += " `household_accept_leave` has one bar for accept and one for leave."
    out = [
        "## 1. Latency by pair type (full data, fraction 1)",
        "",
        "![Median latency by case](latency_by_case.png)",
        "",
        f"{axis}; each bar is the chart value, the thin line runs to the largest p95. {charted}",
    ]
    n = 0
    for section in SECTIONS:
        cases = [c for c in section["cases"] if c in run.cases]
        if not cases:
            continue
        n += 1
        title = section["title"]
        if section.get("titled"):
            title += ": " + join_and([f"`{c}`" for c in cases])
        out += ["", f"### 1.{n} {title}", ""]
        kind = section.get("kind")
        if kind == "admin":
            out += admin_section(run)
            continue
        if kind == "household":
            out += household_section(run)
            continue
        out.append(LATENCY_HEAD)
        for case in cases:
            out += latency_rows(run, case)
        out += ["", f"Reading: {'; '.join(verdict(run, c) for c in cases)}."]
        if section.get("note") and section.get("note_if", cases[0]) in cases:
            out += ["", section["note"]]
    return out


# ------------------------------------------------------------------------------------------------ the rest


def power_line(run: Run) -> list[str]:
    """The power source and profile, when bench/run.sh recorded them, over every invocation of every fraction."""
    invocations = []
    for env_file in sorted(run.folder.glob("*/env.json")):
        invocations += json.loads(env_file.read_text(encoding="utf-8"))["invocations"]
    powers = [i["power"] for i in invocations if i.get("power")]
    if not powers:
        return []
    seen = list(dict.fromkeys((p["source"], p["profile"]) for p in powers))
    said = "; ".join(f"{source}, power profile `{profile}`" for source, profile in seen)
    if len(powers) == len(invocations):
        scope = "every invocation" if len(seen) == 1 else "the invocations, in order of first appearance"
        return [f"- Power: {said}, recorded by `bench/run.sh` for {scope} ({len(powers)} in all)."]
    missing = join_and(sorted({i["kind"] for i in invocations if not i.get("power")}))
    return [
        f"- Power: {said}, recorded by `bench/run.sh` for {len(powers)} of {len(invocations)} invocations; the "
        f"{missing} invocation did not record it (the recording was added to `bench.explain` after this run)."
    ]


def conditions(run: Run, host: str) -> list[str]:
    env = run.env
    psycopg = env["connections"]["postgresql"]["driver"]
    pg_version = env["postgresql"]["version"]
    mem = env["docker_memory_bytes"] / 2**30
    limits = env["mem_limits"]
    sales = env["sale_counts"]
    verified = [i for i in env["invocations"] if i.get("verify")]
    checks = {i["verify"]["checks"] for i in verified}
    kinds = join_and([i["kind"] for i in verified])
    cache_pg, cache_mongo = env["postgresql"]["shared_buffers"], env["mongodb"]["wiredtiger_cache_gb"]
    same = " the same 1 GB for both." if cache_pg == "1GB" and cache_mongo == "1" else ""
    return [
        "## Conditions and limits",
        "",
        "- Warm cache only: every call is repeated a number of times first and no cache is flushed between runs. No "
        "cold-cache result exists.",
        f"- {HOSTS[host]}",
        *power_line(run),
        f"- Commit `{env['git_commit'][:7]}`, PostgreSQL {pg_version}, MongoDB {env['mongodb']['version']}, {psycopg} "
        f"and pymongo as drivers. Docker was given {mem:.1f} GiB of memory and the host reported {env['cpu_count']} "
        "CPUs in `env.json`.",
        f"- Caches: PostgreSQL `shared_buffers` {cache_pg} and MongoDB WiredTiger cache {cache_mongo} GB (read from "
        f"the running container),{same} Container memory limits: PostgreSQL {limits['postgres'] // 2**20:,} MiB, "
        f"MongoDB {limits['mongodb'] // 2**20:,} MiB.",
        f"- Data: {sales['pg']:,} sales in PostgreSQL and {sales['mongo']:,} in MongoDB at fraction "
        f"{env['fraction']:g}; `bench.verify` passed ({', '.join(f'{c:,}' for c in sorted(checks))} checks) before "
        f"the {kinds} invocations; the index on and off invocation (`bench.explain`) does not run it.",
        "- The `web` service was stopped for the whole run, so its healthcheck did not query either database during "
        "timing.",
        "- Timed reads ran as PostgreSQL `app_ro` (`SELECT` only) and MongoDB `app`. These identities are not "
        "symmetric: MongoDB `app` can also write `buyers` and `counters`, which the harness never does. Write cases "
        "run as `app_admin` in both databases.",
        "- MongoDB has an extra `{town, flat_type, txn_month}` index that PostgreSQL has no equivalent of. It favours "
        "MongoDB in `search_filtered`. Compare an index-on-off result of one database only with itself.",
        "- MongoDB server times are whole milliseconds (`executionTimeMillis`, or the last stage's estimate for an "
        "aggregation), so a sub-millisecond call reads as 0 or 1. Server time is taken from `EXPLAIN` and is a "
        "different measurement from the client time (median, p95), which wraps the whole repo call including "
        "materialising the result. Write cases have client timing only.",
        "- Each case has 3 parameter sets of 50 timed runs per database. Tables give every parameter set. Where a "
        "chart needs one number per case and database, the chart value is the median of the three parameter sets' "
        "medians, and the p95 whisker is the largest of the three p95 values. Each table below ends each case with a "
        "`chart "
        "value` row (median, p95) so every bar label can be found; tables print milliseconds to 4 dp exactly as the "
        "CSVs do, and the chart labels round the chart value to 1 dp.",
    ]


def throughput(run: Run) -> list[str]:
    rows = read_csv(run.folder / "f100" / "throughput.csv")
    workers = sorted({int(r["workers"]) for r in rows})
    seconds = round(_median([float(r["seconds"]) for r in rows]))
    cases = list(dict.fromkeys(r["case"] for r in rows))
    out = [
        "## 2. Throughput (full data)",
        "",
        "![Throughput by clients](throughput.png)",
        "",
        f"Each level ran for about {seconds} s with {join_and([str(w) for w in workers])} worker processes (one client "
        "each), on the same machine as the databases, so clients and servers share the CPUs.",
        "",
        "| Case | Database | Clients | Calls | Calls/s | Median ms | p95 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| `{r['case']}` | {DB_NAMES[r['db']]} | {r['workers']} | {count(r['calls'])} | "
            f"{float(r['calls_per_s']):,.1f} | {ms(float(r['median_ms']))} | {ms(float(r['p95_ms']))} |"
        )
    lo, hi = workers[0], workers[-1]
    readings = []
    for case in cases:
        rate = {(r["db"], int(r["workers"])): float(r["calls_per_s"]) for r in rows if r["case"] == case}
        parts = [
            f"{DB_NAMES[db]} {rate[db, lo]:,.1f} calls/s at {lo} client{'' if lo == 1 else 's'} and "
            f"{rate[db, hi]:,.1f} at {hi} ({rate[db, hi] / rate[db, lo]:.1f} times)"
            for db in ("pg", "mongo")
        ]
        ratios = ", ".join(f"{rate['mongo', w] / rate['pg', w]:.1f} at {w}" for w in workers)
        readings.append(f"`{case}`: {'; '.join(parts)}. MongoDB calls/s over PostgreSQL calls/s: {ratios} clients.")
    out += [
        "",
        f"Reading: {' '.join(readings)} This is {words(len(cases))} read cases only, on one machine, with the extra "
        "MongoDB index favouring `search_filtered`; it is not a capacity figure.",
    ]
    return out


def scaling(run: Run) -> list[str]:
    rows = read_csv(run.folder / "scaling.csv")
    fractions = sorted({float(r["fraction"]) for r in rows})
    envs = {}
    for sub in run.folder.iterdir():
        if (sub / "env.json").is_file():
            env = json.loads((sub / "env.json").read_text(encoding="utf-8"))
            envs[round(float(env["fraction"]), 2)] = env
    loads = []
    for f in fractions:
        env = envs[f]
        sales = env["sale_counts"]
        n = f"{sales['pg']:,}" if sales["pg"] == sales["mongo"] else f"{sales['pg']:,} and {sales['mongo']:,}"
        pct = f"{f * 100:g} per cent"
        loads.append(n + (" in both databases" if not loads else "") + f" at {pct}" + (
            f" (fraction {env['fraction']:.6f})" if f < 1 else ""
        ))  # fmt: skip
    cases = [
        c for c in run.cases
        if all(any(float(r["fraction"]) == f and r["case"] == c for r in rows) for f in fractions)
    ]  # fmt: skip
    out = [
        "## 3. Scaling with the amount of data",
        "",
        "![Median latency at 25, 50 and 100 per cent of the sales](scaling.png)",
        "",
        f"Loaded sales: {join_and(loads)}. Only the sales are filtered; the buildings, stats and everything else are "
        f"the full snapshot. Only the {words(len(cases))} cases run at all three fractions are charted and "
        "tabulated; `scaling.csv` also holds the other full-data rows.",
        "",
        "| Case | Database | "
        + " | ".join(f"Chart value {f * 100:g}% ms" for f in fractions)
        + f" | {fractions[-1] * 100:g}% over {fractions[0] * 100:g}% | "
        + " | ".join(f"Set medians at {f * 100:g}% ms" for f in fractions)
        + " |",
        "|---|---|" + "---|" * (2 * len(fractions) + 1),
    ]
    growth = {}
    for case in cases:
        for db in ("pg", "mongo"):
            charts, sets = [], []
            for f in fractions:
                found = [r for r in rows if float(r["fraction"]) == f and r["case"] == case and r["db"] == db
                         and r["part"] == "call"]  # fmt: skip
                medians = [float(r["median_ms"]) for r in found]
                charts.append(_median(medians))
                sets.append(" / ".join(ms(m) for m in medians))
            growth[case, db] = charts[-1] / charts[0]
            out.append(
                f"| `{case}` | {DB_NAMES[db]} | "
                + " | ".join(f"**{ms(c)}**" for c in charts)
                + f" | {growth[case, db]:.2f} | "
                + " | ".join(sets)
                + " |"
            )
    readings = [
        f"`{c}`: chart value at 100 per cent over chart value at 25 per cent is {growth[c, 'pg']:.2f} for PostgreSQL "
        f"and {growth[c, 'mongo']:.2f} for MongoDB"
        for c in cases
    ]
    out += [
        "",
        "Parameter sets in the last three columns are in the order of the full-data tables in section 1 (`/` "
        "separates the three sets).",
        "",
        f"Reading: {'; '.join(readings)}. Each set median is over 50 calls; the chart value is the median of the "
        "three.",
    ]
    return out


INDEX_SCAN = re.compile(r"(?:Index Only Scan|Bitmap Index Scan|Index Scan|IXSCAN|COUNT_SCAN|DISTINCT_SCAN) (\S+)")


def plan_indexes(statement: str) -> list[str]:
    """Non-key indexes a plan uses, in plan order (primary keys and MongoDB's _id_ are left out)."""
    found = [i for i in INDEX_SCAN.findall(statement) if not i.endswith("_pkey") and i != "_id_"]
    return list(dict.fromkeys(found))


def plan_text(plan: str) -> str:
    statements = plan.split(" | ")
    labels = ["count", "page"] if len(statements) == 2 else ["statement"] * len(statements)
    parts = []
    for label, statement in zip(labels, statements, strict=True):
        found = plan_indexes(statement)
        parts.append(f"{label}: " + (", ".join(f"`{i}`" for i in found) if found else "no non-key index"))
    return "; ".join(parts)


def index_on_off(run: Run) -> list[str]:
    rows = read_csv(run.folder / "f100" / "index_on_off.csv")
    groups = list(dict.fromkeys((r["case"], r["db"]) for r in rows))
    out = [
        "## 4. Index on and off",
        "",
        "![Median latency with and without the index](index_on_off.png)",
        "",
        "`bench.explain` runs each case with and without its index, one method for both arms, as `neighboar_owner` "
        "(PostgreSQL, inside a transaction that is always rolled back) and `etl` (MongoDB, index recreated "
        "afterwards). Each database is compared only with itself; MongoDB also has the `{town, flat_type, txn_month}`"
        " index and the two sets of indexes are not equivalent. The chart value is again the median of the three "
        "parameter sets' "
        "medians. The index named in the table is the one dropped in the off arm.",
        "",
        "| Case | Database | Index dropped | On: chart value ms | On: largest p95 ms | Off: chart value ms "
        "| Off: largest p95 ms | Off / on |",
        "|---|---|---|---|---|---|---|---|",
    ]
    slower = []
    for case, db in groups:
        arm = {a: [r for r in rows if (r["case"], r["db"], r["arm"]) == (case, db, a)] for a in ("on", "off")}
        cv = {a: _median([float(r["median_ms"]) for r in arm[a]]) for a in arm}
        p95 = {a: max(float(r["p95_ms"]) for r in arm[a]) for a in arm}
        out.append(
            f"| `{case}` | {DB_NAMES[db]} | `{arm['on'][0]['index']}` | **{ms(cv['on'])}** | {ms(p95['on'])} | "
            f"**{ms(cv['off'])}** | {ms(p95['off'])} | {cv['off'] / cv['on']:.1f} |"
        )
        slower.append(
            f"`{case}` {DB_NAMES[db]} {cv['off'] / cv['on']:.1f} times slower without the index "
            f"({ms(cv['on'])} ms to {ms(cv['off'])} ms)"
        )
    out += [
        "",
        "### Plans, per parameter set",
        "",
        "Plans are from `EXPLAIN` of the captured statements (`f100/explain/`). `search_*` cases run two statements, a "
        "count and a page; `median_exact` runs one. PostgreSQL primary-key lookups are left out of the index list. A "
        "plan with no non-key index scans the table.",
        "",
        "| Case | Database | Parameter set | Arm | Median ms | p95 ms | Server ms | Dropped index in plan "
        "| Indexes in plan |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| `{r['case']}` | {DB_NAMES[r['db']]} | {r['param_set']} | {r['arm']} | {ms(float(r['median_ms']))} | "
            f"{ms(float(r['p95_ms']))} | {server(r)} | {'yes' if r['index_in_plan'] == 'True' else 'no'} | "
            f"{plan_text(r['plan'])} |"
        )
    readings = []
    for case, db in groups:
        mine = [r for r in rows if (r["case"], r["db"]) == (case, db)]
        dropped = mine[0]["index"]
        on, off = [r for r in mine if r["arm"] == "on"], [r for r in mine if r["arm"] == "off"]
        has = {a: sum(r["index_in_plan"] == "True" for r in arm) for a, arm in (("on", on), ("off", off))}
        fallback = list(dict.fromkeys(
            i for r in off for s in r["plan"].split(" | ") for i in plan_indexes(s) if i != dropped
        ))  # fmt: skip
        bare = sum(all(not plan_indexes(s) for s in r["plan"].split(" | ")) for r in off)
        text = (
            f"`{case}`, {DB_NAMES[db]}: `{dropped}` is in the plan in {has['on']} of {len(on)} sets with it and "
            f"{has['off']} of {len(off)} without it; "
        )
        text += (
            "without it the plans name " + ", ".join(f"`{i}`" for i in fallback)
            if fallback
            else "without it no plan names a non-key index"
        )
        if fallback and bare:
            text += f" and {bare} set(s) name no non-key index at all"
        readings.append(text)
    out += [
        "",
        f"Reading: {'; '.join(readings)}.",
        "",
        "Without the `(flat_type, month)` index both databases fall back to the neighbouring price index where the "
        "plan still uses an index (`resale_transaction_type_price_idx` in PostgreSQL, `type_price` in MongoDB), as "
        "the rows above show for each parameter set. For `search_price_sort` "
        "the dropped index is the price index and the fallback is the month index.",
        "",
        f"Reading of the latencies: {'; '.join(slower)}.",
    ]
    return out


def admin_refresh(run: Run) -> list[str]:
    case = "admin_record_delete"
    out = [
        "## 5. `admin_record_delete`: write and refresh separately",
        "",
        "Each timed run records one sale and then deletes it (`bench/cases.yaml`). PostgreSQL times the write and the "
        "materialised view refresh separately; MongoDB records and deletes in transactions that recompute the block "
        "stats, so it has no separate refresh part. The refresh is not charted. Client timing only.",
        "",
        "| Parameter set | PG write median ms | PG write p95 ms | PG refresh median ms | PG refresh p95 ms "
        "| Mongo write median ms | Mongo write p95 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for ps in run.sets(case):
        cells = [ps]
        for db, part in (("pg", "write"), ("pg", "refresh"), ("mongo", "write")):
            r = run.row(case, db, ps, part)
            cells += [ms(float(r["median_ms"])), ms(float(r["p95_ms"]))]
        out.append("| " + " | ".join(cells) + " |")
    charts = {k: run.chart(case, *k) for k in (("pg", "write"), ("pg", "refresh"), ("mongo", "write"))}
    out.append(
        "| **chart value (write); refresh is the same rule** | "
        + " | ".join(f"**{ms(v)}**" for cv in charts.values() for v in cv)
        + " |"
    )
    out += [
        "",
        f"Reading: the PostgreSQL write has a chart value of {ms(charts['pg', 'write'][0])} ms and MongoDB's write "
        f"{ms(charts['mongo', 'write'][0])} ms; the PostgreSQL refresh that follows has a chart value of "
        f"{ms(charts['pg', 'refresh'][0])} ms. The two sides recompute different things: the PostgreSQL refresh is "
        "`REFRESH ... CONCURRENTLY` of the whole town-month view over all "
        f"{run.env['sale_counts']['pg']:,} sales, whereas MongoDB recomputes the stats of one block inside the record "
        "and delete transactions. Neither pairing (write against write, or MongoDB write against PostgreSQL write plus "
        "refresh) is a like-for-like speed comparison.",
    ]
    return out


FILES = [
    "## 6. Files",
    "",
    "- `f100/summary.csv`, `f100/throughput.csv`, `f100/index_on_off.csv`, `scaling.csv`: the tables above.",
    "- `f100/raw.jsonl`, `f50/raw.jsonl`, `f25/raw.jsonl`: every timed call.",
    "- `f100/env.json`, `f50/env.json`, `f25/env.json`: software, settings, invocations and verify results.",
    "- `f100/explain/`: captured statements and plans (text and JSON), including the index-off arms.",
    "- `latency_by_case`, `throughput`, `scaling`, `index_on_off` as `.png` (300 dpi, 16 cm wide) and `.svg`, from "
    "`python -m bench.charts`.",
]


def build(folder: Path, host: str) -> str:
    run = Run(folder)
    lines = [
        f"# Benchmark results, {folder.name}",
        "",
        "Every number in this file is generated from the committed `summary.csv`, `throughput.csv`, "
        "`index_on_off.csv`, `scaling.csv` and `env.json` files in this folder tree; none is typed by hand. The raw "
        "timings are in `f100/raw.jsonl` (and `f50/`, `f25/`).",
        "",
        *conditions(run, host),
        "",
        *section_one(run),
        "",
        *throughput(run),
        "",
        *scaling(run),
        "",
        *index_on_off(run),
        "",
        *admin_refresh(run),
        "",
        *FILES,
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path, help="a results folder holding f100/, f50/, f25/ and scaling.csv")
    ap.add_argument("--host", choices=sorted(HOSTS), required=True, help="the operating system the run used")
    ap.add_argument("--out", type=Path, help="where to write (default: <folder>/RESULTS.md)")
    args = ap.parse_args(argv)
    text = build(args.folder, args.host)
    out = args.out or args.folder / "RESULTS.md"
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
