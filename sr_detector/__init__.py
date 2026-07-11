"""
sr_detector/ — Trading System v2 · S&R Detector V1 (SNR-DETECTOR-V1)

A PURE package (imports only core/ + stdlib) that runs as an async, non-gating
observer beside order placement. It fetches Daily/60m/30m structure for each
PLACED candidate, scores support/resistance zones by confluence, flags
"buying-into-resistance" (and the support/short mirror), and logs an evidence
row to sr_detector_results for OFFLINE validation. SHADOW only — it never
rejects, sizes, or touches SL/TGT/STM, and never blocks/delays placement.

Public API:
    build_sr_detector(...)  → wire from config + injected collaborators
    SRDetector              → the observer (observe / start / stop)
    Candidate               → the post-placement snapshot the seam builds
"""
from __future__ import annotations

from sr_detector.detector import DETECTOR_VERSION, SRDetector
from sr_detector.fetch import OhlcFetcher
from sr_detector.models import Candidate

__all__ = ["build_sr_detector", "SRDetector", "OhlcFetcher", "Candidate", "DETECTOR_VERSION"]


def build_sr_detector(
    *,
    config,
    fetch_fn,
    instrument_cache,
    store,
    logger,
    mode: str,
    now_fn=None,
    session_open=None,
    session_close=None,
):
    """
    Construct a wired SRDetector. `fetch_fn(token, from_date, to_date, interval)`
    is the rate-limited broker call (built in main.py as a closure over the
    market-data kite handle + rate_limiter — see main.py wiring). `now_fn`
    defaults to core.time_authority.now_ist.

    `session_open`/`session_close` (datetime.time) are the NSE session bounds
    used by the V3 03.01 Layer-A anchors (VWAP/ORB); pass them from the system's
    MarketWindows (main.py) so nothing hardcodes 09:15. They are optional — when
    absent (or `intraday_anchors_enabled` is False, the default) the detector
    skips the intraday-anchor fetch and behaves exactly as before.
    """
    if now_fn is None:
        from core.time_authority import now_ist
        now_fn = now_ist

    fetcher = OhlcFetcher(
        fetch_fn=fetch_fn,
        instrument_cache=instrument_cache,
        lookback_days=int(getattr(config, "lookback_days", 180)),
        logger=logger,
        now_fn=now_fn,
        cache_ttl_sec=float(getattr(config, "cache_ttl_sec", 1800.0)),
    )
    return SRDetector(
        config=config,
        fetcher=fetcher,
        store=store,
        logger=logger,
        mode=mode,
        now_fn=now_fn,
        session_open=session_open,
        session_close=session_close,
    )
