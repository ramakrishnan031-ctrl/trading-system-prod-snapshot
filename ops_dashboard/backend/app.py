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

from .api.capacity import capacity_api
from .api.dashboard import dashboard_api
from .api.pipeline import pipeline_api
from .api.strategy import strategy_api
from .auth import LoginAttemptTracker, auth_bp

_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.abspath(os.path.join(_HERE, "..", "frontend"))
_DEFAULT_CONFIG = os.path.join(_HERE, "config", "gui_config.yaml")

_LOGIN_EXEMPT = {"auth.login_get", "auth.login_post"}


def load_gui_config(config_path: str = _DEFAULT_CONFIG) -> dict:
    with open(config_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


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
    app.register_blueprint(strategy_api)

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
