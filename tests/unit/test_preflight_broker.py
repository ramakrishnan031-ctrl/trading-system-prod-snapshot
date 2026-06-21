"""
tests/unit/test_preflight_broker.py -- Group 4 (broker). File/network checks +
live-call checks via an injected fake BrokerProbe (no real kite session).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scripts.preflight.base import CheckContext, Status
from scripts.preflight.checks import broker

AS_OF = date(2026, 6, 22)


def _ctx(tmp_path: Path, mode="live", dry_run=False, probe="__none__") -> CheckContext:
    (tmp_path / "data_store" / "session").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    extra = {} if probe == "__none__" else {"broker_probe": probe}
    return CheckContext(config_dir=cfg, db_path=tmp_path / "data_store" / "trading_system.db",
                        mode=mode, dry_run=dry_run, as_of_date=AS_OF, extra=extra)


def _token(tmp_path, access="x"):
    p = tmp_path / "data_store" / "session" / "zerodha_token.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"access_token": access}), encoding="utf-8")
    return p


class _FakeProbe:
    def __init__(self, prof=None, marg=None, orders=None, raise_on=()):
        self._prof = prof if prof is not None else {"user_id": "AB1234"}
        self._marg = marg if marg is not None else {"available": {"cash": 10247.0}}
        self._orders = orders if orders is not None else []
        self._raise = set(raise_on)

    def profile(self):
        if "profile" in self._raise:
            raise RuntimeError("403 IP not allowed")
        return self._prof

    def margins(self):
        if "margins" in self._raise:
            raise RuntimeError("margins err")
        return self._marg

    def orders(self):
        if "orders" in self._raise:
            raise RuntimeError("orders err")
        return self._orders


# ── token file/freshness ─────────────────────────────────────────────────────────
def test_token_file_exists(tmp_path):
    assert broker.TokenFileExistsCheck().run(_ctx(tmp_path, mode="paper")).status is Status.SKIPPED
    assert broker.TokenFileExistsCheck().run(_ctx(tmp_path)).status is Status.FAIL
    _token(tmp_path)
    assert broker.TokenFileExistsCheck().run(_ctx(tmp_path)).status is Status.PASS


def test_token_fresh_and_fix(tmp_path, monkeypatch):
    _token(tmp_path)
    st = {"d": date(2026, 6, 21)}
    monkeypatch.setattr(broker, "_file_mdate", lambda p: st["d"])
    chk = broker.TokenFreshCheck()
    assert chk.run(_ctx(tmp_path)).status is Status.FAIL

    def _refresh():
        st["d"] = AS_OF
        return True
    monkeypatch.setattr(broker, "_trigger_token_refresh", _refresh)
    fr = chk.fix(_ctx(tmp_path))
    assert fr.success
    assert chk.run(_ctx(tmp_path)).status is Status.PASS


def test_instruments_fresh_grading(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (ctx.config_dir / "instruments.csv").write_text("symbol,token\n" + "X,1\n" * 200, encoding="utf-8")
    monkeypatch.setattr(broker, "_file_mdate", lambda p: AS_OF)
    chk = broker.InstrumentsFreshCheck()
    monkeypatch.setattr(broker, "_trading_days_behind", lambda m, a, c: 0)
    assert chk.run(ctx).status is Status.PASS                 # refreshed today
    monkeypatch.setattr(broker, "_trading_days_behind", lambda m, a, c: 1)
    assert chk.run(ctx).status is Status.WARN                 # last trading day, refresh pending
    monkeypatch.setattr(broker, "_trading_days_behind", lambda m, a, c: 3)
    assert chk.run(ctx).status is Status.FAIL                 # missed -> CRITICAL


def test_instruments_fix(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (ctx.config_dir / "instruments.csv").write_text("symbol,token\n", encoding="utf-8")
    st = {"d": date(2026, 6, 1)}
    monkeypatch.setattr(broker, "_file_mdate", lambda p: st["d"])
    monkeypatch.setattr(broker, "_trading_days_behind", lambda m, a, c: 0 if st["d"] == AS_OF else 5)
    chk = broker.InstrumentsFreshCheck()
    assert chk.run(ctx).status is Status.FAIL
    monkeypatch.setattr(broker, "_trigger_instruments_refresh", lambda a: st.update(d=AS_OF) or True)
    assert chk.fix(ctx).success
    assert chk.run(ctx).status is Status.PASS


# ── public IP ─────────────────────────────────────────────────────────────────────
def test_vm_ip_record_then_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "_get_public_ip", lambda: "1.2.3.4")
    chk = broker.VmIpUnchangedCheck()
    r1 = chk.run(_ctx(tmp_path))
    assert r1.status is Status.PASS and "baseline" in r1.detail
    r2 = chk.run(_ctx(tmp_path))
    assert r2.status is Status.PASS and "unchanged" in r2.detail


def test_vm_ip_changed_is_critical(tmp_path, monkeypatch):
    store = tmp_path / "data_store" / "preflight" / "last_known_ip.txt"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("9.9.9.9", encoding="utf-8")
    monkeypatch.setattr(broker, "_get_public_ip", lambda: "1.2.3.4")
    res = broker.VmIpUnchangedCheck().run(_ctx(tmp_path))
    assert res.status is Status.FAIL and "changed" in res.detail


def test_vm_ip_unknown_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "_get_public_ip", lambda: None)
    assert broker.VmIpUnchangedCheck().run(_ctx(tmp_path)).status is Status.WARN


def test_vm_ip_dry_run_does_not_record(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "_get_public_ip", lambda: "1.2.3.4")
    ctx = _ctx(tmp_path, dry_run=True)
    assert broker.VmIpUnchangedCheck().run(ctx).status is Status.PASS
    assert not (tmp_path / "data_store" / "preflight" / "last_known_ip.txt").exists()


# ── holiday ───────────────────────────────────────────────────────────────────────
def test_holiday_today(tmp_path, monkeypatch):
    import utils.holiday_guard as hg
    monkeypatch.setattr(hg, "is_trading_day", lambda d, c: True)
    assert broker.HolidayTodayCheck().run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(hg, "is_trading_day", lambda d, c: False)
    monkeypatch.setattr(hg, "get_holiday_name", lambda d, c: "Test Holiday")
    assert broker.HolidayTodayCheck().run(_ctx(tmp_path)).status is Status.FAIL


# ── live-call checks (fake probe) ─────────────────────────────────────────────────
def test_profile_call(tmp_path):
    assert broker.ProfileCallCheck().run(_ctx(tmp_path, mode="paper")).status is Status.SKIPPED
    assert broker.ProfileCallCheck().run(_ctx(tmp_path)).status is Status.SKIPPED  # no probe
    assert broker.ProfileCallCheck().run(_ctx(tmp_path, probe=_FakeProbe())).status is Status.PASS
    assert broker.ProfileCallCheck().run(
        _ctx(tmp_path, probe=_FakeProbe(raise_on=("profile",)))).status is Status.FAIL


def test_margins_call(tmp_path):
    assert broker.MarginsCallCheck().run(_ctx(tmp_path, probe=_FakeProbe())).status is Status.PASS
    assert broker.MarginsCallCheck().run(
        _ctx(tmp_path, probe=_FakeProbe(raise_on=("margins",)))).status is Status.FAIL


def test_funds_available(tmp_path):
    assert broker.FundsAvailableCheck().run(_ctx(tmp_path, probe=_FakeProbe())).status is Status.PASS
    assert broker.FundsAvailableCheck().run(
        _ctx(tmp_path, probe=_FakeProbe(marg={"available": {"cash": 0.0}}))).status is Status.FAIL
    assert broker.FundsAvailableCheck().run(
        _ctx(tmp_path, probe=_FakeProbe(marg={}))).status is Status.WARN


def test_orders_endpoint(tmp_path):
    assert broker.OrdersEndpointCheck().run(_ctx(tmp_path, probe=_FakeProbe())).status is Status.PASS
    assert broker.OrdersEndpointCheck().run(
        _ctx(tmp_path, probe=_FakeProbe(raise_on=("orders",)))).status is Status.WARN


# ── probe builder ─────────────────────────────────────────────────────────────────
def test_build_probe_paper_is_none(tmp_path):
    assert broker.build_broker_probe(_ctx(tmp_path, mode="paper")) is None


def test_build_probe_no_token_is_none(tmp_path):
    assert broker.build_broker_probe(_ctx(tmp_path)) is None  # no token file
