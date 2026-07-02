"""Read-only data readers for the ops dashboard (DB / trader-metrics / config / host).

Isolation: nothing in this package imports a production trading package.
db_reader is the ONLY SQLite touchpoint and opens every connection read-only.
"""
