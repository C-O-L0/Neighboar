"""bench.report: RESULTS.md comes from the committed CSVs and env.json files alone."""

from pathlib import Path

from bench import report

RESULTS = Path(__file__).resolve().parents[2] / "bench" / "results"


def test_it_reproduces_the_committed_report_of_the_first_full_run():
    folder = RESULTS / "2026-11-full"
    assert report.build(folder, "windows") == (folder / "RESULTS.md").read_text(encoding="utf-8")


def test_plan_indexes_leave_out_keys_and_keep_plan_order():
    plan = "Limit > Index Scan building_pkey > Bitmap Index Scan b_idx > Index Scan a_idx > Index Scan b_idx"
    assert report.plan_indexes(plan) == ["b_idx", "a_idx"]
    assert report.plan_indexes("FETCH > IXSCAN _id_") == []
    assert report.plan_text("GROUP > COUNT_SCAN type_month | SORT > FETCH > COLLSCAN") == (
        "count: `type_month`; page: no non-key index"
    )
