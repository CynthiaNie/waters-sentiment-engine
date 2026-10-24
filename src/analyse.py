"""Aggregation and scoring.

Every table reports base counts (n) alongside percentages. Cells with n below
MIN_CELL are labelled 'insufficient data' rather than shown as unstable percentages.
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

from . import db

MIN_CELL = int(os.getenv("MIN_CELL", "10"))

CODE_ORDER = ["W", "A", "T", "E", "R", "S", "COST", "OTHER"]
CODE_NAMES = {
    "W": "Wellbeing", "A": "Availability", "T": "Taste", "E": "Environment",
    "R": "Reassurance", "S": "Social and self-image", "COST": "Cost",
    "OTHER": "Other",
}


def _frame() -> pd.DataFrame:
    """Flat mention-level frame joined to product and source."""
    with db.connect() as con:
        rows = con.execute("""
            SELECT m.id AS mention_id, m.waters_code, m.sentiment, m.intensity,
                   m.confidence, m.evidence_span,
                   COALESCE(m.evidence_span_en, m.evidence_span) AS evidence_span_en,
                   d.id AS document_id, d.language, d.posted_at, d.rating, d.url,
                   COALESCE(p.brand, 'Unattributed') AS brand,
                   COALESCE(p.category, 'unknown')   AS category,
                   s.name AS source_name, s.platform
            FROM mentions m
            JOIN documents d ON d.id = m.document_id
            LEFT JOIN products p ON p.id = d.product_id
            JOIN sources  s ON s.id = d.source_id
            WHERE s.in_scope = 1
        """).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["need"] = df["waters_code"].map(CODE_NAMES).fillna(df["waters_code"])
    df["is_pos"] = (df["sentiment"] == "positive").astype(int)
    df["is_neg"] = (df["sentiment"] == "negative").astype(int)
    df["posted_month"] = (
        pd.to_datetime(df["posted_at"], errors="coerce", utc=True)
        .dt.tz_localize(None).dt.to_period("M").astype(str))
    return df


def _suppress(df: pd.DataFrame, n_col: str = "n") -> pd.DataFrame:
    out = df.copy()
    mask = out[n_col] < MIN_CELL
    for col in out.columns:
        if col in (n_col,) or out[col].dtype == object:
            continue
        out.loc[mask, col] = pd.NA
    out["note"] = mask.map({True: f"insufficient data (n<{MIN_CELL})", False: ""})
    return out


# ------------------------------------------------------------------ 1. volume
def mention_volume(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    overall = (df.groupby("need").size().reindex(
        [CODE_NAMES[c] for c in CODE_ORDER]).fillna(0).astype(int)
        .rename("n").reset_index())
    overall["share_pct"] = (overall["n"] / max(overall["n"].sum(), 1) * 100).round(1)
    by_brand = (df.pivot_table(index="brand", columns="need", values="mention_id",
                               aggfunc="count", fill_value=0).reset_index())
    by_cat = (df.pivot_table(index="category", columns="need", values="mention_id",
                             aggfunc="count", fill_value=0).reset_index())
    return {"overall": overall, "by_brand": by_brand, "by_category": by_cat}


# --------------------------------------------------------------- 2. net sentiment
def net_sentiment(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["brand", "need"]).agg(
        n=("mention_id", "count"),
        pos=("is_pos", "sum"),
        neg=("is_neg", "sum"),
        w_pos=("intensity", lambda s: (df.loc[s.index, "is_pos"] * s).sum()),
        w_neg=("intensity", lambda s: (df.loc[s.index, "is_neg"] * s).sum()),
    ).reset_index()
    g["net_sentiment"] = ((g["pos"] - g["neg"]) / g["n"]).round(3)
    # A cell whose mentions are all neutral has zero weight, and dividing by it
    # is undefined rather than zero. float("nan") keeps the column numeric;
    # pd.NA would promote it to object dtype and break .round() below.
    wt = (g["w_pos"] + g["w_neg"]).astype(float).replace(0, float("nan"))
    g["net_sentiment_weighted"] = ((g["w_pos"] - g["w_neg"]) / wt).round(3)
    return _suppress(g.sort_values(["brand", "need"]))


# --------------------------------------------------------------- 3. share of voice
def share_of_voice(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    total = len(df)
    overall = (df.groupby("brand").size().rename("n").reset_index()
               .sort_values("n", ascending=False))
    overall["share_pct"] = (overall["n"] / max(total, 1) * 100).round(1)
    per_need = df.pivot_table(index="brand", columns="need", values="mention_id",
                              aggfunc="count", fill_value=0)
    per_need_pct = (per_need.div(per_need.sum(axis=0).replace(0, pd.NA), axis=1)
                    * 100).round(1).reset_index()
    return {"overall": overall, "per_need_pct": per_need_pct}


# ---------------------------------------------------- 4. pain concentration score
def pain_concentration(df: pd.DataFrame) -> pd.DataFrame:
    """mention frequency x negative share. The review-mining analogue of the
    prevalence x urgency matrix."""
    total = len(df)
    g = df.groupby(["brand", "need"]).agg(
        n=("mention_id", "count"), neg=("is_neg", "sum")).reset_index()
    g["mention_freq_pct"] = (g["n"] / max(total, 1) * 100).round(2)
    g["negative_share"] = (g["neg"] / g["n"]).round(3)
    g["pain_score"] = (g["mention_freq_pct"] * g["negative_share"]).round(2)
    return _suppress(g.sort_values("pain_score", ascending=False))


# ------------------------------------------------------ 5. category substitution
def category_substitution(df: pd.DataFrame) -> pd.DataFrame:
    """WATERS profile of jug filters against dispensers. Shows which unmet need
    drives trading up from a jug to a dispenser."""
    keep = df[df["category"].isin(["jug_filter", "countertop_dispenser", "purifier"])]
    if keep.empty:
        return pd.DataFrame(columns=["need", "category", "n", "share_within_cat_pct",
                                     "net_sentiment"])
    g = keep.groupby(["category", "need"]).agg(
        n=("mention_id", "count"), pos=("is_pos", "sum"), neg=("is_neg", "sum")
    ).reset_index()
    tot = g.groupby("category")["n"].transform("sum")
    g["share_within_cat_pct"] = (g["n"] / tot * 100).round(1)
    g["net_sentiment"] = ((g["pos"] - g["neg"]) / g["n"]).round(3)
    wide = g.pivot(index="need", columns="category",
                   values="share_within_cat_pct").fillna(0).reset_index()
    wide.columns.name = None
    if {"jug_filter", "countertop_dispenser"} <= set(wide.columns):
        wide["gap_dispenser_minus_jug"] = (
            wide["countertop_dispenser"] - wide["jug_filter"]).round(1)
    return wide


# ------------------------------------------------------------------ 6. time series
def time_series(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["posted_month"].notna() & (df["posted_month"] != "NaT")]
    if d.empty:
        return pd.DataFrame(columns=["posted_month", "need", "n", "net_sentiment"])
    g = d.groupby(["posted_month", "need"]).agg(
        n=("mention_id", "count"), pos=("is_pos", "sum"), neg=("is_neg", "sum")
    ).reset_index()
    g["net_sentiment"] = ((g["pos"] - g["neg"]) / g["n"]).round(3)
    return g.sort_values(["posted_month", "need"])


# ------------------------------------------------------------ 7. evidence appendix
def evidence_appendix(df: pd.DataFrame, per_cell: int = 3) -> pd.DataFrame:
    if df.empty:
        return df
    d = df.sort_values("confidence", ascending=False)
    out = (d.groupby(["brand", "need", "sentiment"], group_keys=False)
             .head(per_cell)
             [["brand", "need", "sentiment", "intensity", "confidence",
               "evidence_span", "evidence_span_en", "source_name", "url"]])
    return out.sort_values(["brand", "need", "sentiment"])


def run_all() -> dict[str, pd.DataFrame]:
    df = _frame()
    if df.empty:
        raise RuntimeError("No mentions found. Run collection and classification first.")
    vol = mention_volume(df)
    sov = share_of_voice(df)
    return {
        "Mention_Volume": vol["overall"],
        "Volume_By_Brand": vol["by_brand"],
        "Volume_By_Category": vol["by_category"],
        "Net_Sentiment": net_sentiment(df),
        "Share_of_Voice": sov["overall"],
        "SoV_Per_Need": sov["per_need_pct"],
        "Pain_Concentration": pain_concentration(df),
        "Category_Substitution": category_substitution(df),
        "Time_Series": time_series(df),
        "Evidence_Appendix": evidence_appendix(df),
        "Raw_Mentions": df.drop(columns=["is_pos", "is_neg"]),
    }
