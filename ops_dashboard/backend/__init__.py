"""ops_dashboard backend — isolated, read-only Flask app (M1 skeleton + Dashboard).

Isolation (Rama-mandated I1-I6): NO imports from production packages; SQLite is
opened read-only only; no broker; binds 127.0.0.1:8500 loopback only.
"""
