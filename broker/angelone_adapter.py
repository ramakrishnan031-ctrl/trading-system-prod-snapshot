"""
broker/angelone_adapter.py -- Trading System v2  FIX-133 Item 28

Purpose:
    Stub adapter for AngelOne broker as a fallback.
    Same interface as ZerodhaAdapter but raises NotImplementedError
    until AngelOne credentials are available and integration is tested.

Locked Design:
    AO1  -- Stub only: all methods raise NotImplementedError.
    AO2  -- Same method signatures as ZerodhaAdapter.
    AO3  -- Enable via system_config.yaml broker.fallback_enabled: true.
    AO4  -- Intended for future: AngelOne SmartAPI integration.
"""
from __future__ import annotations

from typing import Any, Optional


class AngelOneAdapter:
    """
    Stub broker adapter for AngelOne (AO1-AO4).

    All methods raise NotImplementedError with a clear message.
    Wire this as the fallback adapter in order_placer when
    broker.fallback_enabled is set to true.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._configured = False

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str,
        price: float = 0.0,
        trigger_price: float = 0.0,
        product: str = "MIS",
        tag: str = "",
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError(
            "AngelOne adapter is a stub. Configure AngelOne credentials "
            "and implement SmartAPI integration before enabling fallback."
        )

    def cancel_order(self, broker_order_id: str, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "AngelOne adapter is a stub. Cancel not implemented."
        )

    def modify_order(
        self,
        broker_order_id: str,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        qty: Optional[int] = None,
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError(
            "AngelOne adapter is a stub. Modify not implemented."
        )

    def get_positions(self) -> list:
        raise NotImplementedError(
            "AngelOne adapter is a stub. Positions not implemented."
        )

    def get_order_status(self, broker_order_id: str) -> Any:
        raise NotImplementedError(
            "AngelOne adapter is a stub. Order status not implemented."
        )

    def get_margins(self) -> dict:
        raise NotImplementedError(
            "AngelOne adapter is a stub. Margins not implemented."
        )
