"""
v3_chain — Trading System v2 · V3 Step 10 decision-chain SHADOW enrichment.

Step 10a: run the V3 chain (generic gates + Context/Execution score) over live signals
in shadow, LOG-ONLY, to measure the S&R R:R gate against real outcomes (G-KALYAN).
Default-OFF (v3_chain.v3_chain_mode) → not constructed → byte-identical live path.

Public surface kept small; main.py imports V3ChainRunner, signal_processor imports the
V3Signal snapshot type.
"""
from __future__ import annotations

from v3_chain.models import V3Signal, WouldBeRecord
from v3_chain.runner import V3ChainRunner

__all__ = ["V3ChainRunner", "V3Signal", "WouldBeRecord"]
