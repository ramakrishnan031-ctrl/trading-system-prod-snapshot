# C-2 Prep: Chartink Source-IP Investigation (READ-ONLY)

Date: 2026-07-03 (Friday), ~22:30 IST, off-market. Investigation only — zero
code changes, zero config changes, nothing pushed. Facts for Web Claude +
Rama's later C-2 (Option A: IP allowlist vs Option B: reverse proxy + HMAC)
decision session. **No verdict on A vs B is given here.**

## 1. Sources of recorded inbound webhook IPs

| Source | Verdict | Citation |
|---|---|---|
| `webhook_audit.source_ip` (SQLite) | **Ground truth — used for this report** | Schema: `data_store/trading_system.db` table `webhook_audit`, column `source_ip TEXT NOT NULL`, indexed by `date` (generated column, `substr(ts,1,10)`). Populated at `signals/webhook_receiver.py:341` — `source_ip: str = request.remote_addr or "unknown"`; written via the audit-insert helper at `signals/webhook_receiver.py:776-795`. |
| nginx / reverse-proxy access logs | **Confirmed absent** (matches G0 finding) | `/etc/nginx` does not exist on the VM. No reverse proxy sits in front of the webhook receiver — `request.remote_addr` is the raw TCP peer, **not** an `X-Forwarded-For` header (which would be spoofable/unreliable). This makes `webhook_audit.source_ip` a trustworthy direct-TCP source, not a proxy-relayed one. |
| fail2ban | **No webhook-relevant jail** | `sudo fail2ban-client status` → only jail configured is `sshd`. No jail parses webhook/port-5000 activity, so fail2ban holds no additional IP data for this investigation. |
| App debug/system logs (`logs/debug_*.log`, `logs/system_*.log`) | **Checked, no source-IP data** | `webhook_receiver` log lines are lifecycle-only (e.g. `"webhook_receiver: loaded 2 symbol aliases..."`, `"WebhookReceiver.stop() called..."`) — no `source_ip` field is echoed to these logs. `webhook_audit` is the **sole** source of IP data; there is no secondary log to cross-check against. |

## 2. Distinct source IPs — full window (2026-06-12 → 2026-07-03, 15 trading days, 58,507 total webhook requests)

| source_ip | first_seen | last_seen | hits | distinct scanners hit | response codes seen |
|---|---|---|---|---|---|
| **23.106.53.213** | 2026-06-12 | 2026-07-03 | **58,504** | 13 (full roster) | 403, 200, 503 |
| 23.106.53.222 | 2026-06-18 | 2026-06-18 | 1 | 1 (`positional_swing_long`) | 403 |
| 23.106.53.196 | 2026-06-17 | 2026-06-17 | 1 | 1 (`open_low_breakout_long`) | 403 |
| 127.0.0.1 | 2026-06-17 | 2026-06-17 | 1 | 1 (`open_low_breakout_long`) | 403 |

**23.106.53.213 hit every single one of the 15 trading days** in the window with no gap (per-date counts range 1,931–4,592 hits/day; the 2026-06-19 dip to 1,931 is a known short trading day, not an IP anomaly). 200/403/503 all appear daily for this IP — this is **expected, not an auth or IP problem**: `signals/webhook_receiver.py` has exactly two 403 causes in the code (`:439-441` kill-switch active, `:453-456` outside entry window `10:00–15:00` IST) and one 503 cause (receiver stopped). The high 403 volume is Chartink firing scanner alerts outside the entry window / during kill-switch, which the receiver correctly rejects — it is **not** IP-based rejection (no IP allowlist exists yet to reject on).

**Classification of the 3 outlier single-hit IPs:**
- **23.106.53.196** (2026-06-17T21:30 IST, `open_low_breakout_long`, 369-byte payload, 403): timestamp is well outside the 10:00–15:00 entry window → 403 = outside-window, consistent with a genuine (if late/off-hours) Chartink delivery attempt. Real scanner name + realistic payload size — **looks like genuine Chartink traffic**, not scanner noise.
- **23.106.53.222** (2026-06-18T10:44 IST, `positional_swing_long`, 366-byte payload, 403): timestamp **is inside** the entry window, so by the code's only two 403 paths this 403 must be kill-switch-active at that moment, not a window rejection. Real scanner name + realistic payload — **looks like genuine Chartink traffic** that happened to land during an active kill-switch.
- **127.0.0.1** (2026-06-17T21:33 IST, `open_low_breakout_long`, **0-byte payload**, 403, 3 minutes after the `.196` hit): zero-byte payload from localhost is consistent with a manual/test curl reproduction, not Chartink — **classified as an operator test hit, not Chartink traffic.**

No other scanner/bot noise was observed in the window — total distinct IPs across 58,507 requests is only 4.

## 3. Verdict

**STATIC-so-far: 1 IP (23.106.53.213), unchanged across the full observed range 2026-06-12 → 2026-07-03 (15 trading days, 58,504 of 58,507 total hits, 99.99%).**

Caveat (fact, not a recommendation): 2 single-occurrence hits from IPs in the **same /24 as the main IP** (23.106.53.222, 23.106.53.196) appeared once each, both carrying real scanner names and realistic payloads consistent with genuine Chartink deliveries, both rejected for reasons unrelated to IP (window/kill-switch). Whether Chartink's infra occasionally uses sibling IPs in its pool cannot be concluded from 2 data points in 15 days — flagging as **INSUFFICIENT DATA to rule out occasional pool-IP variation**, alongside the otherwise-static verdict for the dominant IP.

## 4. CIDR / ASN clustering

All 3 non-localhost IPs resolve to the **same block and the same organization**:

| IP | CIDR (whois) | ASN | Org |
|---|---|---|---|
| 23.106.53.213 (main) | 23.106.48.0/21 | AS59253 | Leaseweb Singapore Pte. Ltd. (Leaseweb Asia Pacific) |
| 23.106.53.222 (outlier) | 23.106.48.0/21 | AS59253 | Leaseweb Singapore Pte. Ltd. |
| 23.106.53.196 (outlier) | 23.106.48.0/21 | AS59253 | Leaseweb Singapore Pte. Ltd. |

All three sit inside the same `/24` (`23.106.53.0/24`), itself inside Leaseweb's `/21` (`23.106.48.0/21`). Country registration is AU (APNIC parent record) with the operating entity registered in Singapore (SG). This is a hosting-provider block (Leaseweb), consistent with Chartink running its alert-delivery infra on rented cloud/VPS capacity rather than a fixed corporate egress range — relevant context for A-vs-B, but the decision itself is deferred to Web Claude + Rama per scope.

## 5. Deviations from the instruction

- None. Read-only throughout; no firewall/code/config change; nothing pushed tonight.
