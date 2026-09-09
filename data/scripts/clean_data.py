#!/usr/bin/env python3
"""
clean_data.py — Kathmandu Bus Route Finder data-cleaning pipeline.

Turns the raw exports in data/raw/ into validated tables in data/processed/,
and regenerates processed/report.md documenting exactly what changed.

Usage:
    python data/scripts/clean_data.py \
        --raw-dir data/raw \
        --out-dir data/processed
"""

from __future__ import annotations

import argparse
import difflib
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("clean_data")

UTC_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Kathmandu Valley bounding box. Stops outside this area are flagged,
# not automatically removed.
VALLEY_BBOX = {
    "lat_min": 27.55,
    "lat_max": 27.85,
    "lng_min": 85.15,
    "lng_max": 85.55,
}

LOOP_KEYWORDS = ("loop", "parikrama")

# A short A -> B -> A backtrack may indicate a splice artifact.
REVISIT_BACKTRACK_SUSPECT_M = 400


@dataclass
class CleaningStats:
    """Everything needed to regenerate report.md."""

    rows_before: dict[str, int] = field(default_factory=dict)
    rows_after: dict[str, int] = field(default_factory=dict)

    orphan_route_stops_removed: int = 0
    phantom_stop_ids: dict[str, dict] = field(default_factory=dict)
    resequenced_routes: int = 0

    start_stop_corrected: list[str] = field(default_factory=list)
    end_stop_corrected: list[str] = field(default_factory=list)
    total_stops_corrected: list[str] = field(default_factory=list)

    invalid_operator_ids: list[str] = field(default_factory=list)
    operator_id_nulled_routes: list[str] = field(default_factory=list)

    revisit_rows: int = 0
    revisit_routes: int = 0

    distance_flagged_routes: list[str] = field(default_factory=list)

    verification: dict[str, int] = field(default_factory=dict)

    stop_dedup_groups: list[list[str]] = field(default_factory=list)
    stop_dedup_dropped: int = 0
    stop_dedup_candidates: list[list[str]] = field(default_factory=list)
    stop_dedup_pending_review: list[list[str]] = field(default_factory=list)

    route_dedup_merged: list[tuple[str, str]] = field(default_factory=list)
    route_dedup_marked_bidirectional: list[str] = field(default_factory=list)
    route_dedup_candidates: list[dict] = field(default_factory=list)
    route_dedup_pending_review: list[dict] = field(default_factory=list)

    revisit_candidates: list[dict] = field(default_factory=list)
    revisit_confirmed_dropped_rows: int = 0
    revisit_confirmed_kept_routes: list[str] = field(default_factory=list)
    revisit_confirmed_collapsed_routes: list[str] = field(default_factory=list)
    revisit_pending_review: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------
# Generic type cleaning
# ---------------------------------------------------------------------

def clean_int_columns(
    df: pd.DataFrame,
    columns: list[str],
    required: list[str] | None = None,
) -> pd.DataFrame:
    """
    Convert integer-like values safely.

    Examples:
        "7"   -> 7
        "7.0" -> 7
        ""    -> <NA>

    Non-integer numeric values such as "7.5" raise an error.
    """
    required = required or []

    for col in columns:
        if col not in df.columns:
            continue

        raw = df[col].replace("", pd.NA)
        numeric = pd.to_numeric(raw, errors="coerce")

        invalid_text = raw.notna() & numeric.isna()
        if invalid_text.any():
            examples = raw[invalid_text].head(5).tolist()
            raise ValueError(
                f"{col}: invalid integer value(s): {examples}"
            )

        non_integer = numeric.notna() & (numeric % 1 != 0)
        if non_integer.any():
            examples = raw[non_integer].head(5).tolist()
            raise ValueError(
                f"{col}: non-integer value(s): {examples}"
            )

        if col in required and numeric.isna().any():
            examples = raw[numeric.isna()].head(5).tolist()
            raise ValueError(
                f"{col}: required integer contains missing value(s): {examples}"
            )

        df[col] = numeric.astype("Int64")

    return df


def clean_float_columns(
    df: pd.DataFrame,
    columns: list[str],
    required: list[str] | None = None,
) -> pd.DataFrame:
    """
    Convert numeric values to floats.

    Examples:
        "15"   -> 15.0
        "15.5" -> 15.5
        ""     -> NaN
    """
    required = required or []

    for col in columns:
        if col not in df.columns:
            continue

        raw = df[col].replace("", pd.NA)
        numeric = pd.to_numeric(raw, errors="coerce")

        invalid = raw.notna() & numeric.isna()
        if invalid.any():
            examples = raw[invalid].head(5).tolist()
            raise ValueError(
                f"{col}: invalid numeric value(s): {examples}"
            )

        if col in required and numeric.isna().any():
            examples = raw[numeric.isna()].head(5).tolist()
            raise ValueError(
                f"{col}: required numeric column contains missing values: {examples}"
            )

        df[col] = numeric

    return df


def clean_bool_columns(
    df: pd.DataFrame,
    columns: list[str],
    default: bool = False,
) -> pd.DataFrame:
    """
    Normalize boolean representations.

    Accepted true values:
        true, 1, yes, y

    Accepted false values:
        false, 0, no, n, empty
    """
    true_values = {"true", "1", "yes", "y"}
    false_values = {"false", "0", "no", "n", ""}

    for col in columns:
        if col not in df.columns:
            continue

        raw = df[col].fillna("").astype(str).str.strip().str.lower()

        invalid = ~raw.isin(true_values | false_values)
        if invalid.any():
            examples = raw[invalid].head(5).tolist()
            raise ValueError(
                f"{col}: invalid boolean value(s): {examples}"
            )

        result = pd.Series(default, index=df.index)
        result.loc[raw.isin(true_values)] = True
        result.loc[raw.isin(false_values)] = False

        df[col] = result.astype(bool)

    return df


# ---------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------

def _normalize_stop_name(name: str) -> str:
    """Strip common boilerplate so name variants compare cleanly."""
    n = str(name).strip().lower()

    for suffix in (
        " chowk / junction",
        " chowk/junction",
        " stop",
        " station",
    ):
        if n.endswith(suffix):
            n = n[: -len(suffix)]

    return n.strip()


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance in km between two coordinates."""
    r = 6371.0088

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dlambda / 2) ** 2
    )

    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------
# Stop deduplication
# ---------------------------------------------------------------------

def dedup_stops(
    stops: pd.DataFrame,
    stats: CleaningStats,
    overrides_path: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:

    DEDUP_RADIUS_M = 250

    df = stops.reset_index(drop=True)
    n = len(df)

    if n < 2:
        return df, {}

    lat = df["lat"].to_numpy()
    lng = df["lng"].to_numpy()

    id_to_idx = {
        sid: i
        for i, sid in enumerate(df["stop_id"])
    }

    dist_matrix = np.zeros((n, n))

    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(
                lat[i],
                lng[i],
                lat[j],
                lng[j],
            ) * 1000

            dist_matrix[i, j] = d
            dist_matrix[j, i] = d

    condensed = squareform(
        dist_matrix,
        checks=False,
    )

    z = linkage(
        condensed,
        method="complete",
    )

    cluster_labels = fcluster(
        z,
        t=DEDUP_RADIUS_M,
        criterion="distance",
    )

    candidate_clusters: list[list[str]] = []
    label_groups: dict[int, list[int]] = {}

    for idx, label in enumerate(cluster_labels):
        label_groups.setdefault(label, []).append(idx)

    for members in label_groups.values():
        if len(members) > 1:
            candidate_clusters.append(
                [
                    df.at[idx, "stop_id"]
                    for idx in members
                ]
            )

    stats.stop_dedup_candidates = candidate_clusters

    confirmed: dict[str, str] = {}

    if overrides_path and overrides_path.exists():
        import yaml

        with open(overrides_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for entry in data.get("confirmed_merges", []):
            keeper = entry["keep"]

            for dropped in entry.get("drop", []):
                confirmed[dropped] = keeper

    remap: dict[str, str] = {}

    for dropped_id, keeper_id in confirmed.items():
        if (
            dropped_id not in id_to_idx
            or keeper_id not in id_to_idx
        ):
            log.warning(
                "stop_dedup_overrides.yaml references unknown "
                "stop_id (keep=%s, drop=%s) - skipped",
                keeper_id,
                dropped_id,
            )
            continue

        remap[dropped_id] = keeper_id
        stats.stop_dedup_groups.append(
            [keeper_id, dropped_id]
        )

    unconfirmed_candidates = [
        cluster
        for cluster in candidate_clusters
        if not any(
            sid in confirmed
            for sid in cluster
        )
    ]

    stats.stop_dedup_pending_review = unconfirmed_candidates

    if unconfirmed_candidates:
        log.info(
            "%d candidate duplicate cluster(s) proposed but NOT merged "
            "- needs human review, see report.md and add confirmed pairs "
            "to %s",
            len(unconfirmed_candidates),
            overrides_path,
        )

    stats.stop_dedup_dropped = len(remap)

    keep_indices = [
        i
        for i in range(n)
        if df.at[i, "stop_id"] not in remap
    ]

    deduped = (
        df.iloc[keep_indices]
        .reset_index(drop=True)
    )

    return deduped, remap


def remap_stop_ids(
    route_stops: pd.DataFrame,
    remap: dict[str, str],
) -> pd.DataFrame:

    if not remap:
        return route_stops

    df = route_stops.copy()

    df["stop_id"] = df["stop_id"].map(
        lambda sid: remap.get(sid, sid)
    )

    return df


# ---------------------------------------------------------------------
# Route deduplication
# ---------------------------------------------------------------------

def _stop_set_similarity(
    a: frozenset,
    b: frozenset,
) -> float:

    if not a and not b:
        return 1.0

    return len(a & b) / len(a | b)


def dedup_routes(
    routes: pd.DataFrame,
    route_stops: pd.DataFrame,
    route_operators: pd.DataFrame,
    stats: CleaningStats,
    overrides_path: Path | None = None,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    set[str],
]:

    similarity_threshold = 0.7

    routes = routes.copy()

    routes = clean_bool_columns(
        routes,
        ["is_bidirectional"],
    )

    if "is_bidirectional" not in routes.columns:
        routes["is_bidirectional"] = False

    ordered = (
        route_stops
        .sort_values("sequence_no")
        .groupby("route_id")["stop_id"]
        .apply(tuple)
    )

    routes["stop_seq_tmp"] = routes["route_id"].map(
        lambda rid: ordered.get(rid, ())
    )

    routes["stop_set_tmp"] = routes["stop_seq_tmp"].map(
        frozenset
    )

    candidates: list[dict] = []

    by_operator = routes.groupby(
        "operator_id",
        dropna=False,
    )

    for operator_id, group in by_operator:
        if pd.isna(operator_id) or len(group) < 2:
            continue

        rows = list(
            group[
                [
                    "route_id",
                    "route_name",
                    "stop_seq_tmp",
                    "stop_set_tmp",
                ]
            ].itertuples(index=False)
        )

        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a = rows[i]
                b = rows[j]

                sim = _stop_set_similarity(
                    a.stop_set_tmp,
                    b.stop_set_tmp,
                )

                if sim < similarity_threshold:
                    continue

                exact_reverse = (
                    a.stop_seq_tmp
                    == tuple(reversed(b.stop_seq_tmp))
                )

                candidates.append(
                    {
                        "route_a": a.route_id,
                        "name_a": a.route_name,
                        "route_b": b.route_id,
                        "name_b": b.route_name,
                        "operator_id": operator_id,
                        "similarity": round(sim, 2),
                        "exact_reverse": exact_reverse,
                    }
                )

    stats.route_dedup_candidates = candidates

    confirmed: dict[str, dict] = {}

    if overrides_path and overrides_path.exists():
        import yaml

        with open(overrides_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for entry in data.get("confirmed_merges", []):
            keeper = entry["keep"]
            bidirectional = entry.get(
                "bidirectional",
                False,
            )

            for dropped in entry.get("drop", []):
                confirmed[dropped] = {
                    "keep": keeper,
                    "bidirectional": bidirectional,
                }

    dropped_route_ids: set[str] = set()

    route_ids = set(routes["route_id"])

    for dropped_id, info in confirmed.items():
        keeper_id = info["keep"]

        if (
            dropped_id not in route_ids
            or keeper_id not in route_ids
        ):
            log.warning(
                "route_dedup_overrides.yaml references unknown "
                "route_id (keep=%s, drop=%s) - skipped",
                keeper_id,
                dropped_id,
            )
            continue

        dropped_route_ids.add(dropped_id)

        stats.route_dedup_merged.append(
            (keeper_id, dropped_id)
        )

        if info["bidirectional"]:
            routes.loc[
                routes["route_id"] == keeper_id,
                "is_bidirectional",
            ] = True

            stats.route_dedup_marked_bidirectional.append(
                keeper_id
            )

    pending = [
        c
        for c in candidates
        if c["route_a"] not in dropped_route_ids
        and c["route_b"] not in dropped_route_ids
        and c["route_a"] not in confirmed
        and c["route_b"] not in confirmed
    ]

    stats.route_dedup_pending_review = pending

    if pending:
        log.info(
            "%d candidate duplicate route pair(s) proposed but NOT merged "
            "- needs human review, see report.md and add confirmed pairs "
            "to %s",
            len(pending),
            overrides_path,
        )

    routes = (
        routes[
            ~routes["route_id"].isin(dropped_route_ids)
        ]
        .drop(
            columns=[
                "stop_seq_tmp",
                "stop_set_tmp",
            ]
        )
        .reset_index(drop=True)
    )

    route_stops = (
        route_stops[
            ~route_stops["route_id"].isin(
                dropped_route_ids
            )
        ]
        .reset_index(drop=True)
    )

    route_operators = (
        route_operators[
            ~route_operators["route_id"].isin(
                dropped_route_ids
            )
        ]
        .reset_index(drop=True)
    )

    return (
        routes,
        route_stops,
        route_operators,
        dropped_route_ids,
    )


# ---------------------------------------------------------------------
# Revisited-stop resolution
# ---------------------------------------------------------------------

def resolve_revisits(
    route_stops: pd.DataFrame,
    stops: pd.DataFrame,
    stats: CleaningStats,
    overrides_path: Path | None = None,
) -> pd.DataFrame:

    df = (
        route_stops
        .sort_values(["route_id", "sequence_no"])
        .reset_index(drop=True)
    )

    stop_coords = stops.set_index("stop_id")[["lat", "lng"]]

    candidates: list[dict] = []

    for route_id, grp in df.groupby("route_id"):
        grp = grp.reset_index(drop=True)

        ids = grp["stop_id"].tolist()

        seen: dict[str, int] = {}
        dup_positions: dict[str, list[int]] = {}

        for i, sid in enumerate(ids):
            if sid in seen:
                dup_positions.setdefault(
                    sid,
                    [seen[sid]],
                ).append(i)
            else:
                seen[sid] = i

        for sid, positions in dup_positions.items():
            first_idx = positions[0]
            second_idx = positions[1]

            bridge_sid = (
                ids[second_idx - 1]
                if second_idx - 1 != first_idx
                else None
            )

            backtrack_m = None

            if (
                bridge_sid
                and sid in stop_coords.index
                and bridge_sid in stop_coords.index
            ):
                a = stop_coords.loc[sid]
                b = stop_coords.loc[bridge_sid]

                backtrack_m = round(
                    haversine_km(
                        a["lat"],
                        a["lng"],
                        b["lat"],
                        b["lng"],
                    ) * 1000,
                    1,
                )

            candidates.append(
                {
                    "route_id": route_id,
                    "stop_id": sid,
                    "sequence_positions": [
                        int(grp.at[p, "sequence_no"])
                        for p in positions
                    ],
                    "bridge_stop_id": bridge_sid,
                    "backtrack_m": backtrack_m,
                    "suspect": (
                        backtrack_m is not None
                        and backtrack_m
                        < REVISIT_BACKTRACK_SUSPECT_M
                    ),
                }
            )

    stats.revisit_candidates = candidates

    revisited_routes = sorted(
        {
            c["route_id"]
            for c in candidates
        }
    )

    verdicts: dict[str, str] = {}

    if overrides_path and overrides_path.exists():
        import yaml

        with open(overrides_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for entry in data.get("routes", []):
            route_id = entry.get("route_id")
            verdict = entry.get("verdict")

            if (
                route_id
                and verdict in ("keep", "drop_repeats")
            ):
                verdicts[route_id] = verdict

            elif route_id:
                log.warning(
                    "return_leg_overrides.yaml: route %s has "
                    "unrecognized verdict %r - ignored",
                    route_id,
                    verdict,
                )

    drop_row_mask = pd.Series(
        False,
        index=df.index,
    )

    for route_id in revisited_routes:
        verdict = verdicts.get(route_id)

        if verdict == "keep":
            stats.revisit_confirmed_kept_routes.append(
                route_id
            )
            continue

        if verdict == "drop_repeats":
            route_mask = df["route_id"] == route_id
            sub = df[route_mask]

            later_duplicates = sub["stop_id"].duplicated(
                keep="first"
            )

            drop_row_mask.loc[sub.index[later_duplicates]] = True

            stats.revisit_confirmed_collapsed_routes.append(
                route_id
            )
            continue

        stats.revisit_pending_review.append(
            {
                "route_id": route_id,
                "revisits": [
                    c
                    for c in candidates
                    if c["route_id"] == route_id
                ],
            }
        )

    if stats.revisit_pending_review:
        log.info(
            "%d route(s) have unresolved stop revisits - needs human "
            "review, see report.md and add verdicts to %s",
            len(stats.revisit_pending_review),
            overrides_path,
        )

    stats.revisit_confirmed_dropped_rows = int(
        drop_row_mask.sum()
    )

    df = df[~drop_row_mask].copy()

    df = df.sort_values(
        ["route_id", "sequence_no"]
    )

    df["sequence_no"] = (
        df.groupby("route_id").cumcount() + 1
    ).astype("Int64")

    remaining = (
        df.groupby(["route_id", "stop_id"])
        .size()
    )

    remaining = remaining[remaining > 1]

    stats.revisit_rows = int(
        (remaining - 1).sum()
    )

    stats.revisit_routes = (
        int(
            remaining.index
            .get_level_values("route_id")
            .nunique()
        )
        if len(remaining)
        else 0
    )

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------

def _drop_trailing_blank_columns(
    df: pd.DataFrame,
    source: Path,
) -> pd.DataFrame:

    unnamed_cols = [
        c
        for c in df.columns
        if str(c).startswith("Unnamed:")
    ]

    if not unnamed_cols:
        return df

    def blank(col) -> bool:
        return (
            df[col].isna().all()
            or (
                df[col]
                .astype(str)
                .str.strip()
                .eq("")
                .all()
            )
        )

    trim = 0

    for col in reversed(df.columns):
        if (
            str(col).startswith("Unnamed:")
            and blank(col)
        ):
            trim += 1
        else:
            break

    if trim == len(unnamed_cols):
        dropped = list(df.columns[-trim:])

        log.warning(
            "%s: dropped %d trailing blank column(s) %s - "
            "raw export has stray trailing commas",
            source,
            trim,
            dropped,
        )

        return df.iloc[:, :-trim]

    bad_mask = df[unnamed_cols].notna().any(axis=1)
    bad_rows = df.index[bad_mask].tolist()

    lines = [
        (
            f"DataFrame row {i} "
            f"(raw line {i + 2}): "
            f"{df.loc[i].to_dict()}"
        )
        for i in bad_rows[:10]
    ]

    more = (
        f"\n...and {len(bad_rows) - 10} more"
        if len(bad_rows) > 10
        else ""
    )

    raise ValueError(
        f"{source}: {len(bad_rows)} row(s) have real data "
        f"spilling into stray trailing columns {unnamed_cols}. "
        f"Inspect the raw CSV rows:\n"
        + "\n".join(lines)
        + more
    )


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Expected raw file not found: {path}"
        )

    log.info("Loading %s", path)

    df = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )

    return _drop_trailing_blank_columns(df, path)


# ---------------------------------------------------------------------
# Table-specific cleaning
# ---------------------------------------------------------------------

def clean_operators(
    operators: pd.DataFrame,
) -> pd.DataFrame:
    """
    Normalize operators before export.

    Only columns that exist are changed, so this remains compatible
    with the current CSV schema.
    """
    df = operators.copy()

    df = clean_int_columns(
        df,
        [],
    )

    df = clean_float_columns(
        df,
        [],
    )

    df = clean_bool_columns(
        df,
        [
            "is_active",
            "active",
        ],
    )

    return df


def clean_stops(
    stops: pd.DataFrame,
) -> pd.DataFrame:
    """Type-cast stops and flag out-of-bounds coordinates."""
    df = stops.copy()

    # PostgreSQL INTEGER fields.
    df = clean_int_columns(
        df,
        [
            "ward",
        ],
    )

    # PostgreSQL FLOAT / DOUBLE PRECISION fields.
    df = clean_float_columns(
        df,
        [
            "lat",
            "lng",
        ],
        required=[
            "lat",
            "lng",
        ],
    )

    df["geo_out_of_bounds"] = ~(
        df["lat"].between(
            VALLEY_BBOX["lat_min"],
            VALLEY_BBOX["lat_max"],
        )
        & df["lng"].between(
            VALLEY_BBOX["lng_min"],
            VALLEY_BBOX["lng_max"],
        )
    )

    # PostgreSQL BOOLEAN fields.
    df = clean_bool_columns(
        df,
        [
            "is_major_stop",
            "has_shelter",
            "has_ticket_counter",
            "is_interchange",
            "wheelchair_access",
            "audio_support",
        ],
    )

    # geo_out_of_bounds was generated as a real boolean.
    df["geo_out_of_bounds"] = (
        df["geo_out_of_bounds"]
        .astype(bool)
    )

    if "status" in df.columns:
        df["status"] = (
            df["status"]
            .replace("", pd.NA)
            .fillna("active")
        )

    if "created_at" not in df.columns:
        df["created_at"] = UTC_NOW
    else:
        df["created_at"] = (
            df["created_at"]
            .replace("", pd.NA)
            .fillna(UTC_NOW)
        )

    df["updated_at"] = UTC_NOW

    optional_fields = [
        "ward",
        "landmark",
        "has_shelter",
        "has_ticket_counter",
        "wheelchair_access",
        "audio_support",
    ]

    def unverified(row) -> str:
        missing = []

        for field_name in optional_fields:
            value = row.get(field_name, "")

            if pd.isna(value):
                missing.append(field_name)
                continue

            value = str(value).strip()

            if value in (
                "",
                "False",
                "false",
                "nan",
                "<NA>",
            ):
                missing.append(field_name)

        return (
            "{" + ",".join(missing) + "}"
            if missing
            else ""
        )

    if "unverified_fields" not in df.columns:
        df["unverified_fields"] = df.apply(
            unverified,
            axis=1,
        )

    dupes = df["stop_id"].duplicated().sum()

    if dupes:
        log.warning(
            "stops: %d duplicate stop_id rows found - "
            "keeping first occurrence",
            dupes,
        )

        df = df.drop_duplicates(
            subset="stop_id",
            keep="first",
        )

    return df


def clean_route_stops(
    route_stops: pd.DataFrame,
    stops: pd.DataFrame,
    stats: CleaningStats,
    revisit_overrides_path: Path | None = None,
) -> pd.DataFrame:
    """Clean route-stop relationships."""
    df = route_stops.copy()

    # PostgreSQL INTEGER fields.
    df = clean_int_columns(
        df,
        ["sequence_no"],
        required=["sequence_no"],
    )

    valid_stop_ids = set(stops["stop_id"])

    orphan_mask = ~df["stop_id"].isin(
        valid_stop_ids
    )

    orphans = df[orphan_mask]

    stats.orphan_route_stops_removed = int(
        orphan_mask.sum()
    )

    for stop_id, grp in orphans.groupby("stop_id"):
        stop_name = (
            grp["stop_name"].iloc[0]
            if "stop_name" in grp.columns
            else stop_id
        )

        stats.phantom_stop_ids[stop_id] = {
            "name": stop_name,
            "route_count": grp["route_id"].nunique(),
        }

    df = df[~orphan_mask].copy()

    if "stop_name" in df.columns:
        df = df.drop(columns=["stop_name"])

    df = df.sort_values(
        ["route_id", "sequence_no"]
    )

    df["sequence_no"] = (
        df.groupby("route_id").cumcount() + 1
    ).astype("Int64")

    stats.resequenced_routes = df["route_id"].nunique()

    df = resolve_revisits(
        df,
        stops,
        stats,
        overrides_path=revisit_overrides_path,
    )

    return df.reset_index(drop=True)


def clean_routes(
    routes: pd.DataFrame,
    route_stops_clean: pd.DataFrame,
    stops: pd.DataFrame,
    operators: pd.DataFrame,
    route_operators: pd.DataFrame,
    stats: CleaningStats,
) -> pd.DataFrame:
    """Clean routes and normalize all numeric/boolean fields."""
    df = routes.copy()

    # PostgreSQL INTEGER fields.
    df = clean_int_columns(
        df,
        [
            "total_stops",
            "frequency_min",
        ],
    )

    # Numeric route fields.
    df = clean_float_columns(
        df,
        [
            "approx_distance_km",
            "approx_distance_km_original",
            "haversine_distance_km",
            "max_consecutive_stop_jump_km",
        ],
    )

    # Raw boolean fields.
    df = clean_bool_columns(
        df,
        [
            "is_bidirectional",
            "return_leg_verified",
            "status_corrected_for_return_leg",
            "distance_flagged_for_recompute",
        ],
    )

    # -------------------------------------------------------------
    # Recompute start / end / total stops
    # -------------------------------------------------------------

    grouped = (
        route_stops_clean
        .sort_values(["route_id", "sequence_no"])
        .groupby("route_id")
    )

    first_stop = grouped["stop_id"].first()
    last_stop = grouped["stop_id"].last()
    counts = grouped.size()

    for route_id in df["route_id"]:
        if route_id not in counts.index:
            continue

        row_idx = df.index[
            df["route_id"] == route_id
        ][0]

        if (
            df.at[row_idx, "start_stop_id"]
            != first_stop.get(route_id)
        ):
            stats.start_stop_corrected.append(route_id)

            df.at[row_idx, "start_stop_id"] = (
                first_stop[route_id]
            )

        if (
            df.at[row_idx, "end_stop_id"]
            != last_stop.get(route_id)
        ):
            stats.end_stop_corrected.append(route_id)

            df.at[row_idx, "end_stop_id"] = (
                last_stop[route_id]
            )

        recomputed_total = int(counts[route_id])

        old_total = df.at[row_idx, "total_stops"]

        if (
            pd.isna(old_total)
            or int(old_total) != recomputed_total
        ):
            stats.total_stops_corrected.append(route_id)

            df.at[row_idx, "total_stops"] = (
                recomputed_total
            )

    df["total_stops"] = (
        pd.to_numeric(
            df["total_stops"],
            errors="raise",
        )
        .astype("Int64")
    )

    # -------------------------------------------------------------
    # Validate operator IDs
    # -------------------------------------------------------------

    valid_operator_ids = set(
        operators["operator_id"]
    )

    route_op_lookup = (
        route_operators
        .groupby("route_id")["operator_id"]
        .apply(set)
    )

    def resolve_operator(row):
        op_id = row.get("operator_id")

        if (
            pd.isna(op_id)
            or op_id in valid_operator_ids
        ):
            return op_id

        stats.invalid_operator_ids.append(op_id)

        raw = row.get("operator_id_raw")

        if isinstance(raw, str) and raw:
            candidates = [
                value.strip()
                for value in raw.split(";")
                if value.strip()
                in valid_operator_ids
            ]

            if candidates:
                return candidates[0]

        fallback = route_op_lookup.get(
            row["route_id"]
        )

        if fallback:
            valid_fallback = (
                fallback & valid_operator_ids
            )

            if valid_fallback:
                return sorted(valid_fallback)[0]

        stats.operator_id_nulled_routes.append(
            row["route_id"]
        )

        return None

    df["operator_id"] = df.apply(
        resolve_operator,
        axis=1,
    )

    stats.invalid_operator_ids = sorted(
        set(stats.invalid_operator_ids)
    )

    # -------------------------------------------------------------
    # Distance sanity check
    # -------------------------------------------------------------

    stop_coords = stops.set_index(
        "stop_id"
    )[["lat", "lng"]]

    def route_haversine(
        route_id: str,
    ) -> tuple[float, float]:

        seq = (
            route_stops_clean[
                route_stops_clean["route_id"]
                == route_id
            ]
            .sort_values("sequence_no")
        )

        coords = []

        for stop_id in seq["stop_id"]:
            if stop_id in stop_coords.index:
                coords.append(
                    stop_coords.loc[stop_id]
                )

        if len(coords) < 2:
            return 0.0, 0.0

        total = 0.0
        max_jump = 0.0

        for a, b in zip(
            coords,
            coords[1:],
        ):
            distance = haversine_km(
                a["lat"],
                a["lng"],
                b["lat"],
                b["lng"],
            )

            total += distance
            max_jump = max(
                max_jump,
                distance,
            )

        return (
            round(total, 3),
            round(max_jump, 3),
        )

    hav_total = {}
    hav_max = {}

    for route_id in df["route_id"]:
        total, maximum = route_haversine(
            route_id
        )

        hav_total[route_id] = total
        hav_max[route_id] = maximum

    df["haversine_distance_km"] = df[
        "route_id"
    ].map(hav_total)

    df["max_consecutive_stop_jump_km"] = df[
        "route_id"
    ].map(hav_max)

    if "approx_distance_km_original" not in df.columns:
        df["approx_distance_km_original"] = df[
            "approx_distance_km"
        ]

    def flag_distance(row) -> bool:
        try:
            recorded = float(
                row["approx_distance_km"]
            )
            hav = float(
                row["haversine_distance_km"]
            )
        except (
            TypeError,
            ValueError,
        ):
            return False

        if pd.isna(recorded) or pd.isna(hav):
            return False

        if hav == 0:
            return False

        return recorded < hav * 0.9

    flagged = df.apply(
        flag_distance,
        axis=1,
    )

    df["distance_flagged_for_recompute"] = (
        flagged.astype(bool)
    )

    stats.distance_flagged_routes = (
        df.loc[
            flagged,
            "route_id",
        ].tolist()
    )

    df["updated_at"] = UTC_NOW

    if "created_at" not in df.columns:
        df["created_at"] = UTC_NOW
    else:
        df["created_at"] = (
            df["created_at"]
            .replace("", pd.NA)
            .fillna(UTC_NOW)
        )

    # Final normalization before CSV output.
    df = clean_int_columns(
        df,
        [
            "total_stops",
            "frequency_min",
        ],
    )

    df = clean_float_columns(
        df,
        [
            "approx_distance_km",
            "approx_distance_km_original",
            "haversine_distance_km",
            "max_consecutive_stop_jump_km",
        ],
    )

    df = clean_bool_columns(
        df,
        [
            "is_bidirectional",
            "return_leg_verified",
            "status_corrected_for_return_leg",
            "distance_flagged_for_recompute",
        ],
    )

    return df


def apply_default_bidirectional_and_status(
    routes: pd.DataFrame,
) -> pd.DataFrame:
    """Default non-loop routes to bidirectional and all routes to active."""
    df = routes.copy()

    is_loop = (
        df["route_name"]
        .astype(str)
        .str.lower()
        .str.contains(
            "|".join(LOOP_KEYWORDS),
            regex=True,
            na=False,
        )
    )

    df.loc[
        ~is_loop,
        "is_bidirectional",
    ] = True

    df["status"] = "active"

    df = clean_bool_columns(
        df,
        ["is_bidirectional"],
    )

    return df


# ---------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------

def verify(
    routes: pd.DataFrame,
    stops: pd.DataFrame,
    route_stops: pd.DataFrame,
    operators: pd.DataFrame,
    route_operators: pd.DataFrame,
    stats: CleaningStats,
) -> bool:
    """Mirror import integrity checks before PostgreSQL COPY."""

    checks = {
        "route_stops.stop_id not in stops": (
            ~route_stops["stop_id"].isin(
                stops["stop_id"]
            )
        ).sum(),

        "route_stops.route_id not in routes": (
            ~route_stops["route_id"].isin(
                routes["route_id"]
            )
        ).sum(),

        "route_operators.route_id not in routes": (
            ~route_operators["route_id"].isin(
                routes["route_id"]
            )
        ).sum(),

        "route_operators.operator_id not in operators": (
            ~route_operators["operator_id"].isin(
                operators["operator_id"]
            )
        ).sum(),

        "routes.operator_id not in operators (excl. NULL)": (
            routes["operator_id"].notna()
            & ~routes["operator_id"].isin(
                operators["operator_id"]
            )
        ).sum(),

        "routes.start_stop_id not in stops": (
            ~routes["start_stop_id"].isin(
                stops["stop_id"]
            )
        ).sum(),

        "routes.end_stop_id not in stops": (
            ~routes["end_stop_id"].isin(
                stops["stop_id"]
            )
        ).sum(),
    }

    counts = route_stops.groupby(
        "route_id"
    ).size()

    mismatch = routes.apply(
        lambda row: (
            int(row["total_stops"])
            != int(
                counts.get(
                    row["route_id"],
                    0,
                )
            )
        ),
        axis=1,
    ).sum()

    checks[
        "routes.total_stops mismatched vs actual route_stops count"
    ] = int(mismatch)

    stats.verification = {
        name: int(count)
        for name, count in checks.items()
    }

    all_zero = True

    for name, count in checks.items():
        status = "OK" if count == 0 else "FAIL"

        if count != 0:
            all_zero = False

        log.info(
            "verify: %-55s %5d  [%s]",
            name,
            count,
            status,
        )

    return all_zero


# ---------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------

def write_report(
    stats: CleaningStats,
    out_path: Path,
) -> None:

    lines = [
        "# Orphan-pair audit & cleanup report "
        "- Kathmandu Bus Route Finder\n"
    ]

    lines.append(
        f"_Generated {UTC_NOW} by clean_data.py_\n"
    )

    lines.append(
        "| Table | Rows before | Rows after |"
    )
    lines.append("|---|---|---|")

    for table in stats.rows_before:
        lines.append(
            f"| {table} | "
            f"{stats.rows_before[table]} | "
            f"{stats.rows_after.get(table, '?')} |"
        )

    lines.append("")

    lines.append("## 1. route_stops orphan pairs")
    lines.append(
        f"- Removed rows: "
        f"{stats.orphan_route_stops_removed}"
    )

    if stats.phantom_stop_ids:
        lines.append(
            f"- Distinct phantom stop_ids "
            f"({len(stats.phantom_stop_ids)}):"
        )

        for stop_id, info in sorted(
            stats.phantom_stop_ids.items()
        ):
            lines.append(
                f"    - {stop_id} "
                f"(\"{info['name']}\") - "
                f"referenced by {info['route_count']} route(s)"
            )

    lines.append("")

    lines.append("## 2. route_stops re-sequencing")
    lines.append(
        f"- Routes re-sequenced (1..N, order preserved): "
        f"{stats.resequenced_routes}"
    )

    lines.append("")

    lines.append(
        "## 2b. Stop deduplication "
        "(~250m radius candidates)"
    )

    lines.append(
        "- Stops actually merged "
        "(human-confirmed via stop_dedup_overrides.yaml): "
        f"{stats.stop_dedup_dropped}"
    )

    for group in stats.stop_dedup_groups:
        lines.append(
            f"    - kept {group[0]}, "
            f"dropped {group[1:]}"
        )

    lines.append(
        "- Candidate clusters PENDING human review "
        f"(not merged): {len(stats.stop_dedup_pending_review)}"
    )

    for group in stats.stop_dedup_pending_review:
        lines.append(f"    - {group}")

    lines.append("")

    lines.append(
        "## 2c. Route deduplication "
        "(same operator + similar stop set)"
    )

    lines.append(
        "- Routes actually merged "
        "(human-confirmed via route_dedup_overrides.yaml): "
        f"{len(stats.route_dedup_merged)}"
    )

    for keeper, dropped in stats.route_dedup_merged:
        lines.append(
            f"    - kept {keeper}, dropped {dropped}"
        )

    lines.append(
        "- Marked is_bidirectional as a result of merge: "
        f"{stats.route_dedup_marked_bidirectional}"
    )

    lines.append(
        "- Candidate pairs PENDING human review "
        f"(not merged): {len(stats.route_dedup_pending_review)}"
    )

    for candidate in stats.route_dedup_pending_review:
        reverse_note = (
            " [EXACT REVERSE - likely a clean bidirectional pair]"
            if candidate["exact_reverse"]
            else ""
        )

        lines.append(
            f"    - {candidate['route_a']} "
            f"(\"{candidate['name_a']}\") <-> "
            f"{candidate['route_b']} "
            f"(\"{candidate['name_b']}\") "
            f"- stop-set similarity "
            f"{candidate['similarity']}"
            f"{reverse_note}"
        )

    lines.append("")

    lines.append(
        "## 2d. Revisited-stop resolution "
        "(return-leg / splice candidates)"
    )

    lines.append(
        "- Candidate revisit pairs found: "
        f"{len(stats.revisit_candidates)} across "
        f"{len({c['route_id'] for c in stats.revisit_candidates})} "
        "route(s)"
    )

    lines.append(
        "- Rows dropped "
        "(human-confirmed verdict: drop_repeats): "
        f"{stats.revisit_confirmed_dropped_rows}"
    )

    if stats.revisit_confirmed_collapsed_routes:
        lines.append(
            "- Routes collapsed to first occurrence: "
            f"{stats.revisit_confirmed_collapsed_routes}"
        )

    if stats.revisit_confirmed_kept_routes:
        lines.append(
            "- Routes confirmed as genuine loop/return-leg: "
            f"{stats.revisit_confirmed_kept_routes}"
        )

    lines.append(
        "- Routes PENDING human review: "
        f"{len(stats.revisit_pending_review)}"
    )

    lines.append("")

    lines.append(
        "## 3. Route start/end/total stop recomputation"
    )

    lines.append(
        f"- start_stop_id corrected: "
        f"{len(stats.start_stop_corrected)} -> "
        f"{stats.start_stop_corrected}"
    )

    lines.append(
        f"- end_stop_id corrected: "
        f"{len(stats.end_stop_corrected)} -> "
        f"{stats.end_stop_corrected}"
    )

    lines.append(
        f"- total_stops corrected: "
        f"{len(stats.total_stops_corrected)} -> "
        f"{stats.total_stops_corrected}"
    )

    lines.append("")

    lines.append(
        "## 4. routes.operator_id orphan references"
    )

    lines.append(
        f"- Invalid operator_id values: "
        f"{stats.invalid_operator_ids}"
    )

    lines.append(
        f"- Routes nulled (unrecoverable): "
        f"{len(stats.operator_id_nulled_routes)} -> "
        f"{stats.operator_id_nulled_routes}"
    )

    lines.append("")

    lines.append("## 5. Distance outlier flags")

    lines.append(
        "- Routes flagged distance_flagged_for_recompute: "
        f"{len(stats.distance_flagged_routes)} -> "
        f"{stats.distance_flagged_routes}"
    )

    lines.append("")

    lines.append(
        "## 6. Post-cleanup verification "
        "(must all read 0)"
    )

    for name, count in stats.verification.items():
        lines.append(
            f"- {name}: {count}"
        )

    lines.append("")

    lines.append(
        "## 7. Revisited stops remaining after resolution"
    )

    lines.append(
        f"- {stats.revisit_rows} route_stops rows still revisit "
        f"a stop_id across {stats.revisit_routes} route(s)."
    )

    out_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    log.info("Wrote %s", out_path)


# ---------------------------------------------------------------------
# Input files
# ---------------------------------------------------------------------

RAW_FILENAMES = {
    "operators": "operators.csv",
    "stops": "stops_production_v2.csv",
    "routes": "routes_production_v2_fixed.csv",
    "route_stops": "route_stops_production_v2.csv",
    "route_operators": "route_operators_production.csv",
}


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    ap.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw"),
    )

    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/processed"),
    )

    ap.add_argument(
        "--fail-on-verify-error",
        action="store_true",
        help=(
            "Exit non-zero if any post-cleanup check is non-zero"
        ),
    )

    args = ap.parse_args()

    args.out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    stats = CleaningStats()

    # -------------------------------------------------------------
    # Load
    # -------------------------------------------------------------

    operators_raw = load_csv(
        args.raw_dir / RAW_FILENAMES["operators"]
    )

    stops_raw = load_csv(
        args.raw_dir / RAW_FILENAMES["stops"]
    )

    routes_raw = load_csv(
        args.raw_dir / RAW_FILENAMES["routes"]
    )

    route_stops_raw = load_csv(
        args.raw_dir / RAW_FILENAMES["route_stops"]
    )

    route_operators = load_csv(
        args.raw_dir / RAW_FILENAMES["route_operators"]
    )

    for name, df in [
        ("operators.csv", operators_raw),
        ("stops.csv", stops_raw),
        ("routes.csv", routes_raw),
        ("route_stops.csv", route_stops_raw),
        ("route_operators.csv", route_operators),
    ]:
        stats.rows_before[name] = len(df)

    # -------------------------------------------------------------
    # Clean
    # -------------------------------------------------------------

    operators = clean_operators(
        operators_raw
    )

    stops = clean_stops(
        stops_raw
    )

    stops, stop_id_remap = dedup_stops(
        stops,
        stats,
        overrides_path=Path(
            "data/scripts/stop_dedup_overrides.yaml"
        ),
    )

    route_stops_raw = remap_stop_ids(
        route_stops_raw,
        stop_id_remap,
    )

    route_stops = clean_route_stops(
        route_stops_raw,
        stops,
        stats,
        revisit_overrides_path=Path(
            "data/scripts/return_leg_overrides.yaml"
        ),
    )

    (
        routes_raw,
        route_stops,
        route_operators,
        dropped_route_ids,
    ) = dedup_routes(
        routes_raw,
        route_stops,
        route_operators,
        stats,
        overrides_path=Path(
            "data/scripts/route_dedup_overrides.yaml"
        ),
    )

    routes = clean_routes(
        routes_raw,
        route_stops,
        stops,
        operators,
        route_operators,
        stats,
    )

    routes = apply_default_bidirectional_and_status(
        routes
    )

    # -------------------------------------------------------------
    # Final type normalization for relationship tables.
    # -------------------------------------------------------------

    route_stops = clean_int_columns(
        route_stops,
        ["sequence_no"],
        required=["sequence_no"],
    )

    # -------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------

    stats.rows_after["operators.csv"] = len(
        operators
    )
    stats.rows_after["stops.csv"] = len(
        stops
    )
    stats.rows_after["routes.csv"] = len(
        routes
    )
    stats.rows_after["route_stops.csv"] = len(
        route_stops
    )
    stats.rows_after["route_operators.csv"] = len(
        route_operators
    )

    # -------------------------------------------------------------
    # Verify
    # -------------------------------------------------------------

    ok = verify(
        routes,
        stops,
        route_stops,
        operators,
        route_operators,
        stats,
    )

    # -------------------------------------------------------------
    # Final CSV normalization
    # -------------------------------------------------------------

    if "unverified_fields" in operators.columns:
        operators["unverified_fields"] = (
            operators["unverified_fields"].apply(
                lambda value: (
                    "{"
                    + ",".join(
                        field.strip()
                        for field in str(value).split(",")
                        if field.strip()
                    )
                    + "}"
                )
                if str(value).strip()
                else ""
            )
        )

    if "unverified_fields" in stops.columns:
        stops["unverified_fields"] = (
            stops["unverified_fields"].apply(
                lambda value: (
                    "{"
                    + ",".join(
                        field.strip()
                        for field in str(value).split(",")
                        if field.strip()
                    )
                    + "}"
                )
                if str(value).strip()
                else ""
            )
        )

    # -------------------------------------------------------------
    # Write processed files
    # -------------------------------------------------------------

    operators.to_csv(
        args.out_dir / "operators_clean.csv",
        index=False,
    )

    stops.to_csv(
        args.out_dir / "stops_clean.csv",
        index=False,
    )

    routes.to_csv(
        args.out_dir / "routes_clean.csv",
        index=False,
    )

    route_stops.to_csv(
        args.out_dir / "route_stops_clean.csv",
        index=False,
    )

    route_operators.to_csv(
        args.out_dir / "route_operators_clean.csv",
        index=False,
    )

    write_report(
        stats,
        args.out_dir / "report.md",
    )

    log.info(
        "Done. rows_before=%s rows_after=%s",
        stats.rows_before,
        stats.rows_after,
    )

    if args.fail_on_verify_error and not ok:
        log.error(
            "One or more post-cleanup checks failed - see above."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
