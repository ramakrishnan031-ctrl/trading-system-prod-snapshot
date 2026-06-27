"""
sr_detector/zones.py — Trading System v2 · S&R Detector V1

Purpose:
    Cluster same-kind swing pivots (on ONE timeframe) into price BANDS (zones),
    and compute a simple volume-by-price profile to surface high-volume nodes.
    Everything downstream uses zones (with a buffer), never exact prices.
    Pure functions; stdlib only.

Locked Design Decisions:
    SR-Z1 — Greedy 1-D clustering by price: pivots are sorted; a pivot joins the
            running cluster while it is within `cluster_pct` of the cluster's
            current high, else it starts a new cluster.
    SR-Z2 — Zone band = [min, max] of the cluster's pivot prices. touches = count
            of pivots; last_touch_ts = most recent pivot ts (recency).
    SR-Z3 — HIGH pivots → RESISTANCE zones; LOW pivots → SUPPORT zones.
    SR-Z4 — Volume profile bins the bar volume into price buckets (using each
            bar's mid); a "node" is any bin whose volume is >= node_frac of the
            max-volume bin. Returns the node centre prices.
"""
from __future__ import annotations

from typing import List

from sr_detector.models import Candle, Pivot, Zone


def cluster_zones(
    pivots: List[Pivot],
    *,
    cluster_pct: float,
    timeframe: str,
) -> List[Zone]:
    """
    Cluster pivots into zones. RESISTANCE from HIGH pivots, SUPPORT from LOW.

    Args:
        pivots: pivots from one timeframe (mixed HIGH/LOW ok).
        cluster_pct: a pivot joins the running cluster while its price is within
            this percent of the cluster's current band-high (e.g. 0.5 = 0.5%).
        timeframe: label stamped on each produced Zone.
    """
    zones: List[Zone] = []
    for kind, zone_kind in (("HIGH", "RESISTANCE"), ("LOW", "SUPPORT")):
        same = sorted(
            (p for p in pivots if p.kind == kind), key=lambda p: p.price
        )
        if not same:
            continue

        cluster: List[Pivot] = [same[0]]
        for p in same[1:]:
            band_high = max(x.price for x in cluster)
            tol = band_high * (cluster_pct / 100.0)
            if p.price <= band_high + tol:
                cluster.append(p)
            else:
                zones.append(_zone_from_cluster(cluster, zone_kind, timeframe))
                cluster = [p]
        zones.append(_zone_from_cluster(cluster, zone_kind, timeframe))

    return zones


def _zone_from_cluster(cluster: List[Pivot], kind: str, timeframe: str) -> Zone:
    prices = [p.price for p in cluster]
    last_ts = max((p.ts for p in cluster), default=None)
    return Zone(
        band_low=min(prices),
        band_high=max(prices),
        kind=kind,
        touches=len(cluster),
        timeframe=timeframe,
        last_touch_ts=last_ts,
    )


def volume_profile_nodes(
    candles: List[Candle],
    *,
    bins: int = 24,
    node_frac: float = 0.7,
) -> List[float]:
    """
    Return the centre prices of high-volume price bins (volume nodes, SR-Z4).

    A crude volume-by-price: bucket each bar's volume at its mid price across
    `bins` equal-width buckets over [min low, max high]; a node is any bucket
    whose total volume >= node_frac * (max bucket volume).
    """
    if not candles or bins < 1:
        return []
    lo = min(c.low for c in candles)
    hi = max(c.high for c in candles)
    if hi <= lo:
        return []

    width = (hi - lo) / bins
    buckets = [0.0] * bins
    for c in candles:
        mid = (c.high + c.low) / 2.0
        idx = int((mid - lo) / width)
        if idx >= bins:
            idx = bins - 1
        if idx < 0:
            idx = 0
        buckets[idx] += float(c.volume)

    peak = max(buckets)
    if peak <= 0:
        return []
    nodes: List[float] = []
    for i, v in enumerate(buckets):
        if v >= node_frac * peak:
            nodes.append(lo + (i + 0.5) * width)
    return nodes
