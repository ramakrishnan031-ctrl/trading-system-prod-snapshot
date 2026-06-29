"""ops/control_tower/ — VM Operations Control Tower.

An AGGREGATOR that READS existing monitors (security_monitor, Cron Officer,
Config Auditor, excursion reconstruction) and adds data-freshness + disk/backup
checks. Phase 1a ships the FOUNDATION only: the 5 control_tower_* tables
(schema v40) + the security last_run.json status write + this minimal daily
size-logger. The aggregator + freshness checks + report are Phase 1b/1c.
"""
