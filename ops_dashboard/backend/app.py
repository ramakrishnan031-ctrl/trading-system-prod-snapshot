"""
ops_dashboard/backend/app.py

Flask application factory for the ops dashboard.
  * binds 127.0.0.1:8500 ONLY (isolation rule I6) via Waitress
  * login_required on ALL routes incl. static (enforced by a before_request guard;
    only the login GET/POST are exempt — login.html is fully self-contained)
  * hardened session cookie: HttpOnly + SameSite=Strict (+ Secure when TLS is
    present in G2c; on the loopback-HTTP dev box Secure is config-gated so login
    works — see server.session_cookie_secure)

Run:  python -m backend.app
"""
from __future__ import annotations

import os
import secrets
from datetime import timedelta
from typing import Optional

import yaml
from flask import (
    Flask, current_app, jsonify, redirect, render_template, request,
    session, url_for,
)

from .api.analytics import analytics_api
from .api.capacity import capacity_api
from .api.dashboard import dashboard_api
from .api.pipeline import pipeline_api
from .api.risk_capital import risk_capital_api
from .api.strategies import strategies_api
from .api.system import system_api
from .api.trading import trading_api
from .auth import LoginAttemptTracker, auth_bp

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.abspath(os.path.join(_HERE, "..", "frontend"))
_DEFAULT_CONFIG = os.path.join(_HERE, "config", "gui_config.yaml")

_LOGIN_EXEMPT = {"auth.login_get", "auth.login_post"}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursive dict merge — overlay wins on scalar/list conflicts."""
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_gui_config(config_path: str = _DEFAULT_CONFIG) -> dict:
    """Load gui_config.yaml, then deep-merge gui_config.local.yaml if present.

    The LOCAL overlay (git-ignored, chmod 600 on the VM) carries deployment
    values + auth secrets, so the bare-repo hook's `checkout -f` on future
    pushes can never clobber them (G2c deployment survival requirement).
    """
    with open(config_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    local_path = os.path.join(os.path.dirname(config_path), "gui_config.local.yaml")
    if os.path.isfile(local_path):
        with open(local_path, "r", encoding="utf-8") as fh:
            overlay = yaml.safe_load(fh) or {}
        if isinstance(overlay, dict):
            cfg = _deep_merge(cfg, overlay)
    return cfg


def create_app(config_path: Optional[str] = None, gui_config: Optional[dict] = None) -> Flask:
    cfg = gui_config if gui_config is not None else load_gui_config(config_path or _DEFAULT_CONFIG)

    app = Flask(
        __name__,
        template_folder=os.path.join(_FRONTEND, "templates"),
        static_folder=os.path.join(_FRONTEND, "static"),
        static_url_path="/static",
    )
    app.config["GUI_CONFIG"] = cfg

    auth_cfg = cfg.get("auth", {}) or {}
    server_cfg = cfg.get("server", {}) or {}
    app.secret_key = auth_cfg.get("secret_key") or secrets.token_hex(32)
    app.permanent_session_lifetime = timedelta(
        minutes=int(auth_cfg.get("session_lifetime_minutes", 60))
    )
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        # Secure requires HTTPS; enabled in G2c behind TLS. Default off so
        # loopback-HTTP dev login works (V3). Never affects SameSite/HttpOnly.
        SESSION_COOKIE_SECURE=bool(server_cfg.get("session_cookie_secure", False)),
    )
    app.config["LOGIN_TRACKER"] = LoginAttemptTracker(
        max_failures=int(auth_cfg.get("max_failures", 5)),
        lockout_seconds=int(auth_cfg.get("lockout_minutes", 15)) * 60,
    )

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_api)
    app.register_blueprint(pipeline_api)
    app.register_blueprint(capacity_api)
    app.register_blueprint(strategies_api)
    app.register_blueprint(trading_api)
    app.register_blueprint(risk_capital_api)
    app.register_blueprint(system_api)
    app.register_blueprint(analytics_api)

    @app.before_request
    def _enforce_login():
        if request.endpoint in _LOGIN_EXEMPT:
            return None
        if session.get("user"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication required"}), 401
        return redirect(url_for("auth.login_get"))

    @app.route("/", methods=["GET"])
    def dashboard_page():
        return render_template("dashboard.html")

    # G2b-1 screens (Alpine-over-JSON; each fetches its own /api/* endpoint).
    _PAGES = {
        "strategies": "strategies.html", "signals": "signals.html",
        "orders": "orders.html", "positions": "positions.html",
        "holdings": "holdings.html", "capacity": "capacity.html",
        "risk": "risk.html", "capital": "capital.html",
        "exposure": "exposure.html", "pnl": "pnl.html",
        "services": "services.html", "vm": "vm.html", "logs": "logs.html",
        "audit": "audit.html", "alerts": "alerts.html",
        "slippage": "slippage.html", "execution": "execution.html",
        "statistics": "statistics.html", "reports": "reports.html",
        "config": "config.html", "controls": "controls.html",
    }

    @app.route("/<page>", methods=["GET"])
    def module_page(page: str):
        template = _PAGES.get(page)
        if template is None:
            return "Not found", 404
        return render_template(template)

    @app.errorhandler(500)
    def _internal_error(exc):  # noqa: ANN001
        if request.path.startswith("/api/"):
            return jsonify({"error": "internal error"}), 500
        return "Internal error", 500

    return app


def main() -> int:
    from waitress import serve
    app = create_app()
    server = app.config["GUI_CONFIG"].get("server", {})
    host = server.get("bind_host", "127.0.0.1")
    port = int(server.get("bind_port", 8500))
    threads = int(server.get("threads", 4))
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(f"Refusing to bind non-loopback host {host!r} (isolation rule I6).")
    print(f"ops_dashboard serving on http://{host}:{port} (loopback only)")
    serve(app, host=host, port=port, threads=threads)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
