"""The functions the benchmark times.

Read functions take (connection_or_database, args) and call the app's own repos, so the benchmark measures what the
web app runs. Write functions time their own sub-steps and return {part: nanoseconds}. The bench-only statements
(cases.yaml) are built here from bound parameters and typed pipelines, never from strings.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal

from bson.decimal128 import Decimal128

from app.repos.dto import HouseholdOutcome, NewSale, SaleOutcome
from app.repos.mongo.admin import MongoAdminRepo
from app.repos.mongo.building import MongoBuildingRepo
from app.repos.mongo.explore import MongoExploreRepo
from app.repos.mongo.food import MongoFoodRepo
from app.repos.mongo.households import MongoHouseholdRepo
from app.repos.mongo.places import MongoPlaceRepo
from app.repos.mongo.prices import MongoPriceRepo
from app.repos.mongo.rankings import MongoRankingRepo
from app.repos.mongo.search import MongoSearchRepo
from app.repos.mongo.trends import MongoTrendRepo
from app.repos.pg.admin import PgAdminRepo
from app.repos.pg.building import PgBuildingRepo
from app.repos.pg.explore import PgExploreRepo
from app.repos.pg.food import PgFoodRepo
from app.repos.pg.households import PgHouseholdRepo
from app.repos.pg.places import PgPlaceRepo
from app.repos.pg.prices import PgPriceRepo
from app.repos.pg.rankings import PgRankingRepo
from app.repos.pg.search import PgSearchRepo
from app.repos.pg.trends import PgTrendRepo
from app.services.explore import BlocksQuery, FitQuery, block_filter, window_ending
from app.services.prices import Level, Scope
from app.services.prices import window_ending as price_window
from app.services.rankings import RankingQuery
from app.services.search import SearchQuery
from app.services.trends import TrendQuery
from db.mongo.stats import rebuild_all_stats

# ------------------------------------------------------------------------------------------------- repo reads


def pg_search(conn, a):
    return PgSearchRepo(conn).search(SearchQuery.model_validate(a))


def mongo_search(db, a):
    return MongoSearchRepo(db).search(SearchQuery.model_validate(a))


def pg_trends(conn, a):
    return PgTrendRepo(conn).trend(TrendQuery.model_validate(a))


def mongo_trends(db, a):
    return MongoTrendRepo(db).trend(TrendQuery.model_validate(a))


def pg_rankings(conn, a):
    return PgRankingRepo(conn).rank(RankingQuery.model_validate(a))


def mongo_rankings(db, a):
    return MongoRankingRepo(db).rank(RankingQuery.model_validate(a))


def pg_building(conn, a):
    return PgBuildingRepo(conn).get(a["building_id"])


def mongo_building(db, a):
    return MongoBuildingRepo(db).get(a["building_id"])


def pg_food_town(conn, a):
    return PgFoodRepo(conn).town(a["town"])


def mongo_food_town(db, a):
    return MongoFoodRepo(db).town(a["town"])


def _towns_fit(repo, a):
    q = FitQuery.model_validate(a)
    return repo.towns_fit(q.flat_type, q.budget, window_ending(a["end"]))


def pg_towns_fit(conn, a):
    return _towns_fit(PgExploreRepo(conn), a)


def mongo_towns_fit(db, a):
    return _towns_fit(MongoExploreRepo(db), a)


def _blocks_fit(repo, a):
    """The place is the block resolved from block and street (resolve: building), as the place picker gives it."""
    fields = {k: v for k, v in a.items() if k in BlocksQuery.model_fields}
    if "building_id" in a:
        fields["place"] = a["building_id"]
    return repo.blocks_fit(block_filter(BlocksQuery.model_validate(fields), window_ending(a["end"])))


def pg_blocks_fit(conn, a):
    return _blocks_fit(PgExploreRepo(conn), a)


def mongo_blocks_fit(db, a):
    return _blocks_fit(MongoExploreRepo(db), a)


def pg_blocks_near(conn, a):
    return PgPlaceRepo(conn).near(a["building_id"], a["radius_m"])


def mongo_blocks_near(db, a):
    return MongoPlaceRepo(db).near(a["building_id"], a["radius_m"])


def _scope(a) -> Scope:
    return Scope(
        Level(a["level"]), a["building_id"], a["street"], a["town"], a["flat_type"], price_window(a["end"]),
        a.get("storey"),
    )  # fmt: skip


def pg_comparables(conn, a):
    return PgPriceRepo(conn).comparables(_scope(a))


def mongo_comparables(db, a):
    return MongoPriceRepo(db).comparables(_scope(a))


# ----------------------------------------------------------------------------------------- bench-only reads


def _plain(value):
    """Decimal128 to Decimal so results from the two databases compare."""
    return value.to_decimal() if isinstance(value, Decimal128) else value


def sql_call(statement: str, binds: dict, scalar: bool):
    def call(conn):
        rows = conn.execute(statement, binds).fetchall()
        if scalar:
            return rows[0][0] if rows else None
        return rows

    return call


def _finish(docs: list, scalar: str | None):
    if scalar is None:
        return docs
    return _plain(docs[0][scalar]) if docs else None


def pipeline_call(collection: str, pipeline: list, scalar: str | None):
    def call(db):
        return _finish(list(db[collection].aggregate(pipeline)), scalar)

    return call


def find_call(collection: str, filter_: dict, projection: dict | None, scalar: str | None):
    def call(db):
        return _finish(list(db[collection].find(filter_, projection)), scalar)

    return call


# ------------------------------------------------------------------------------------------------ writes


def pg_stats_rebuild(conn, a) -> dict[str, int]:
    t0 = time.perf_counter_ns()
    conn.execute("SELECT refresh_town_stats()")
    return {"call": time.perf_counter_ns() - t0}


def mongo_stats_rebuild(db, a) -> dict[str, int]:
    t0 = time.perf_counter_ns()
    rebuild_all_stats(db)
    return {"call": time.perf_counter_ns() - t0}


def _sale(building_id: int, a: dict) -> NewSale:
    return NewSale(
        building_id,
        a["month"],
        a["flat_type"],
        a["flat_model"],
        a["storey_min"],
        a["storey_max"],
        Decimal(a["floor_area_sqm"]),
        a["remaining_lease_months"],
        Decimal(a["resale_price"]),
    )


def _record_delete(repo, a, refresh=None) -> dict[str, int]:
    """Record one sale and delete it. The finally clause deletes the sale if anything after the insert fails."""
    ref = repo.find_building(a["block"], a["street"])
    if ref is None:
        raise LookupError(f"no block {a['block']} {a['street']}")
    sale = _sale(ref.building_id, a)
    txn_id = None
    write_ns = 0
    refresh_ns = 0
    try:
        t0 = time.perf_counter_ns()
        res = repo.record(sale)
        write_ns += time.perf_counter_ns() - t0
        if res.outcome is not SaleOutcome.OK:
            raise RuntimeError(f"the sale was not recorded: {res.outcome} {res.reason}")
        txn_id = res.txn_id
        t0 = time.perf_counter_ns()
        res = repo.delete(txn_id)
        write_ns += time.perf_counter_ns() - t0
        if res.outcome is not SaleOutcome.OK:
            raise RuntimeError(f"the sale was not deleted: {res.outcome}")
        txn_id = None
        if refresh is not None:
            t0 = time.perf_counter_ns()
            refresh()
            refresh_ns = time.perf_counter_ns() - t0
    finally:
        if txn_id is not None:
            repo.delete(txn_id)
    parts = {"write": write_ns}
    if refresh is not None:
        parts["refresh"] = refresh_ns
    return parts


def pg_admin_record_delete(conn, a) -> dict[str, int]:
    repo = PgAdminRepo(conn)
    return _record_delete(repo, a, refresh=repo.refresh)


def mongo_admin_record_delete(db, a) -> dict[str, int]:
    return _record_delete(MongoAdminRepo(db), a)


# One household round per timed run: an untimed invitation, then accept (merging the partner's blocks) and leave,
# each one transaction and timed as its own part. The two buyers are then put back exactly as they were (personal
# shortlists, last active time, no household), so every round starts from the same state.


def _household_round(repo, email: str, a: dict, restore: Callable[[], None]) -> dict[str, int]:
    inviter, invitee = a["inviter_id"], a["invitee_id"]
    try:
        if repo.invite(inviter, email) is not HouseholdOutcome.OK:
            raise RuntimeError(f"buyer {inviter} could not invite buyer {invitee}")
        t0 = time.perf_counter_ns()
        res = repo.accept(invitee, inviter, True)
        accept_ns = time.perf_counter_ns() - t0
        if res.outcome is not HouseholdOutcome.OK:
            raise RuntimeError(f"the invitation was not accepted: {res.outcome}")
        t0 = time.perf_counter_ns()
        res = repo.leave(invitee)
        leave_ns = time.perf_counter_ns() - t0
        if res is not HouseholdOutcome.OK:
            raise RuntimeError(f"buyer {invitee} could not leave: {res}")
    finally:
        restore()
    return {"accept": accept_ns, "leave": leave_ns}


def _pg_pair(conn, a: dict) -> tuple[str, Callable[[], None]]:
    ids = [a["inviter_id"], a["invitee_id"]]
    busy = conn.execute(
        "SELECT (SELECT count(*) FROM household_member WHERE buyer_id = ANY(%(ids)s))"
        " + (SELECT count(*) FROM household_invitation WHERE inviter_id = ANY(%(ids)s) OR invitee_id = ANY(%(ids)s))",
        {"ids": ids},
    ).fetchone()[0]
    if busy:
        raise RuntimeError(f"buyers {ids} must have no household and no invitation before the household case")
    email = conn.execute("SELECT email FROM buyer WHERE buyer_id = %s", (a["invitee_id"],)).fetchone()[0]
    active = conn.execute("SELECT buyer_id, last_active_at FROM buyer WHERE buyer_id = ANY(%s)", (ids,)).fetchall()
    saved = conn.execute(
        "SELECT buyer_id, building_id, note, saved_at FROM saved_building WHERE buyer_id = ANY(%s)", (ids,)
    ).fetchall()

    def restore():
        with conn.transaction():
            conn.execute(
                "DELETE FROM household WHERE household_id IN"
                " (SELECT household_id FROM household_member WHERE buyer_id = ANY(%s))",
                (ids,),
            )
            conn.execute(
                "DELETE FROM household_invitation WHERE inviter_id = ANY(%(ids)s) OR invitee_id = ANY(%(ids)s)",
                {"ids": ids},
            )
            conn.execute("DELETE FROM saved_building WHERE buyer_id = ANY(%s)", (ids,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO saved_building (buyer_id, building_id, note, saved_at) VALUES (%s, %s, %s, %s)", saved
                )
                cur.executemany("UPDATE buyer SET last_active_at = %s WHERE buyer_id = %s", [(t, b) for b, t in active])

    return email, restore


def pg_household_accept_leave(conn, a) -> dict[str, int]:
    email, restore = _pg_pair(conn, a)
    return _household_round(PgHouseholdRepo(conn), email, a, restore)


RESTORED = ("saved", "last_active_at", "household_id")  # the buyer fields a household round changes


def _mongo_pair(db, a: dict) -> tuple[str, Callable[[], None]]:
    ids = [a["inviter_id"], a["invitee_id"]]
    docs = list(db.buyers.find({"_id": {"$in": ids}}, {"email": 1, **{k: 1 for k in RESTORED}}))
    busy = any(d.get("household_id") is not None for d in docs) or db.invitations.count_documents(
        {"$or": [{"_id": {"$in": ids}}, {"invitee_id": {"$in": ids}}]}
    )
    if len(docs) != 2 or busy:
        raise RuntimeError(f"buyers {ids} must exist with no household and no invitation before the household case")
    email = next(d["email"] for d in docs if d["_id"] == a["invitee_id"])

    def restore():
        db.households.delete_many({"members.buyer_id": {"$in": ids}})
        db.invitations.delete_many({"$or": [{"_id": {"$in": ids}}, {"invitee_id": {"$in": ids}}]})
        for d in docs:
            kept = {k: d[k] for k in RESTORED if k in d}
            gone = {k: "" for k in RESTORED if k not in d}
            back = {op: fields for op, fields in (("$set", kept), ("$unset", gone)) if fields}
            db.buyers.update_one({"_id": d["_id"]}, back)

    return email, restore


def mongo_household_accept_leave(db, a) -> dict[str, int]:
    email, restore = _mongo_pair(db, a)
    return _household_round(MongoHouseholdRepo(db), email, a, restore)
