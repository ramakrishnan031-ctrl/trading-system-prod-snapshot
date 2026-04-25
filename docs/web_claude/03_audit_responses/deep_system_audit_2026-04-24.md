# Trading System v2: Consolidated Deep Audit Report

**Expectation:** review entire file and make suitable audit closures by considering bottlenecks like zerodha api limit and other constrains etc \[Think about optimal resolving method thru skip/modify/change the audit points
**Primary Verdict:** The system is architecturally robust but fails critically at the "Broker API \& Market Physics" layer. The `LIMIT\_TRIPLE` naked short risk, the `IEEE-754` float-drift invariant crash, and the "Ghost Entry" race condition **must** be resolved before live deployment.

\---

## 1\. Critical Race Conditions \& Concurrency Flaws

### 1.1. The "Ghost Entry" Race Condition

* **Location:** `order\_placer.py` ↔ `order\_monitor.py`
* **Risk:** Orders filled instantly (e.g., Market or Cover Orders) trigger a broker WebSocket `OrderFilled` event *before* the `order\_monitor` has registered the `internal\_order\_id` in its `\_watched` list.
* **Consequence:** The fill cannot be linked to a `trade\_id`. The trade remains `PENDING\_FILL` forever in the DB while a real position exists at the broker. Capital is permanently desynchronized.

### 1.2. The "Double Spend" Risk Engine Bypass (TOCTOU)

* **Location:** `signal\_processor.py` ↔ `risk\_engine.py`
* **Risk:** `ThreadPoolExecutor` processes concurrent signals. All threads query the DB (exposure = 0%) *before* any thread reserves capital.
* **Consequence:** 5 concurrent signals for the same sector violate `max\_sector\_exposure\_pct` and `max\_open\_positions` by deploying 100% capital into one sector instead of the intended 20%.

### 1.3. Split-Brain Paradox (Reconciler vs. Monitor)

* **Location:** `order\_monitor.py` ↔ `order\_reconciler.py`
* **Risk:** WebSocket lags (3s) while REST API `get\_positions()` updates instantly. Reconciller sees a position exists (broker) but local DB says `PENDING\_FILL` and adopts the orphan.
* **Consequence:** When WebSocket finally delivers `OrderFilled`, the `FundManager` commits capital twice for a single trade, corrupting the ledger.

\---

## 2\. Catastrophic Financial \& Mathematical Logic Errors

### 2.1. \[CRITICAL] The `LIMIT\_TRIPLE` Naked Short Vulnerability

* **Location:** `order\_protocol\_limit.py` ↔ `order\_placer.py`
* **The Flaw:** Protocol places ENTRY, SL, and TGT orders for full `qty\_requested` upfront.
* **The Exploit:**

  1. Partial fill (100 of 1000 shares) occurs.
  2. Price gaps up violently.
  3. TGT order executes 1000 shares.
* **Net Result:** You hold a **naked short of 900 shares** in a rising stock. The broker RMS does not link the legs. The `RiskEngine` is blind to this infinite-loss position.

### 2.2. \[CRITICAL] IEEE-754 Floating-Point Suicide

* **Location:** `cost\_calculator.py` → `fund\_manager.py` → `invariant.py`
* **The Flaw:** `decimal.Decimal` costs are cast to `float`. `invariant.py` enforces `tolerance = 0.01` (1 paise).
* **The Drift:** Accumulated float drift (e.g., `0.00000000000002` per trade) crosses 0.01 after \~500 trades.
* **Net Result:** `CapitalInvariantViolation` triggers a `HARD\_KILL` due to a rounding artifact, not a real ledger leak.

### 2.3. The "Reserved Capital" Rehydration Amnesia

* **Location:** `fund\_manager.py` ↔ `main.py`
* **Risk:** System crashes with orders `PENDING\_FILL` at broker. `rehydrate()` rebuilds `realized\_pnl` and `used` capital but **sets `reserved = 0`**.
* **Consequence:** `RiskEngine` sees 100% available capital and approves new signals. Existing orders fill, causing a massive, silent margin violation.

### 2.4. The Partial-Fill Black Hole

* **Location:** `order\_monitor.py`
* **Risk:** A limit order is 50% filled. `fill\_timeout\_sec` cancels the remainder. State goes `PARTIAL -> CANCELLED`. `OrderFilled` is only published on `COMPLETE`.
* **Consequence:** The first 50 shares are never committed to capital. The trade is abandoned locally while existing physically at the broker.

\---

## 3\. Broker API \& Market Physics Violations

### 3.1. \[CRITICAL] Cover Order (CO) EOD Square-off Trap

* **Location:** `eod\_squareoff.py`
* **Risk:** Zerodha strictly forbids exiting a CO via reverse `MARKET` order.
* **Consequence:** System fails to close CO positions at 15:17. Broker RMS auto-squares at 15:20, charging heavy penalties (₹50 + GST per order).

### 3.2. The Tick-Size Rejection Loop

* **Location:** `order\_placer.py` ↔ `smart\_tgt\_manager.py`
* **Risk:** Algorithms calculate target/SL prices (e.g., `506.49`) that are not multiples of the instrument’s `tick\_size` (0.05).
* **Consequence:** Zerodha rejects orders with `InputException: Invalid price`. The system spams errors, hits rate limits, and triggers a `SOFT\_KILL`.

### 3.3. The 15:17 Liquidity Vacuum

* **Location:** `eod\_squareoff.py` ↔ `slippage\_model.yaml`
* **Risk:** Using `MARKET` orders at EOD when millions of traders are exiting simultaneously. `slippage\_model.yaml` assumes max 0.3% slippage.
* **Financial Catastrophe:** Orders slip 3-5% instantly on small-caps, obliterating weeks of gains in one second.

### 3.4. Delivery Protocol Mismatch

* **Location:** `positional\_momentum\_long.yaml` vs `order\_protocol\_co.py`
* **Risk:** `LIMIT\_TRIPLE` with `intent: DELIVERY` uses `SL-M` orders.
* **Broker Reality:** Zerodha rejects overnight `SL-M` orders for `CNC/DELIVERY` products at market open or 15:20.
* **Consequence:** Position becomes "naked" without system alert.

\---

## 4\. Resource Management \& Memory Leaks

### 4.1. Shadow Tracker "Zombie Inning" Memory Leak

* **Location:** `shadow\_tracker.py` ↔ `live\_feed.py`
* **Risk:** Simulated innings (Inning 2) created after 15:17 never hit SL/TGT before market close. `eod\_squareoff` does not clean them.
* **Consequence:** List grows by \~50 innings/day. CPU time to process ticks spikes from 1ms → 500ms within weeks, causing WebSocket disconnection.

### 4.2. SQLite WAL Checkpoint Starvation

* **Location:** `state\_store.py`
* **Risk:** Continuous concurrent readers/writers (order\_monitor, signal\_processor) prevent the "quiet lock" needed for WAL checkpointing.
* **Consequence:** `state\_store.db-wal` grows indefinitely, degrading disk I/O until the system locks up.

### 4.3. SQLite Connection Memory Leak

* **Location:** `state\_store.py` ↔ `signal\_processor.py`
* **Risk:** `ThreadPoolExecutor` creates/destroys threads. `threading.local()` connections are never explicitly closed.
* **Consequence:** Zombie connections hold WAL locks and balloon memory usage.

### 4.4. Entry Gate "Amnesia" on Restart

* **Location:** `entry\_gate.py` ↔ `main.py`
* **Risk:** Signals waiting for a pullback are stored only in-memory (`\_watchlist`). System crash wipes this list.
* **Consequence:** High-quality signals are permanently lost, even though DB says `GATE\_WAITING`.

\---

## 5\. Process Flow \& Protocol Failures

### 5.1. Inning Transition Logic Gap

* **Location:** `shadow\_tracker.py`
* **Risk:** `PositionClosed` creates Inning 1 (real). Inning 2 (simulated) relies on `on\_tick` with a 1-second blind spot.
* **Consequence:** Overlapping real and simulated trades if a new signal arrives while shadow inning is active.

### 5.2. The 15:17 Partial-Fill Phantom Share Trap

* **Location:** `eod\_squareoff.py` ↔ `order\_monitor.py`
* **Risk:** A limit order is 50% filled, but `OrderFilled(PARTIAL)` is delayed. `eod\_squareoff` queries DB (qty=0) and only cancels the remainder.
* **Consequence:** System leaves 50 shares orphaned. Broker auto-squares at 15:20 with a ₹50 penalty.

### 5.3. Smart Target "Blind Minute" Vulnerability

* **Location:** `smart\_tgt\_manager.py` ↔ `candle\_store.py`
* **Risk:** Trailing SL advances only on `candle\_close` (1-minute intervals).
* **Consequence:** Stock surges 3% in 15 seconds but reverses within the same minute. SL never trails, leaving gains unprotected.

### 5.4. Event-Bus Thread-Block Cascade

* **Location:** `live\_feed.py` → `candle\_store.py` → `smart\_tgt\_manager.py`
* **Risk:** `adapter.modify\_order()` (synchronous HTTP, \~100ms) runs inside `LiveFeedManager`'s single consumer thread via EventBus callbacks.
* **Consequence:** 10 trailing stops = 1 second block. Tick queue overflows, WebSocket ping/pong drops, broker disconnects the feed.

\---

## 6\. Edge-Case \& Runtime Environment Vulnerabilities

### 6.1. Worker-Death Capital Leak

* **Location:** `webhook\_receiver.py` ↔ `fund\_manager.py`
* **Risk:** A `signal\_processor` thread crashes **after** `FundManager.reserve()` but **before** placing the order.
* **Consequence:** Capital moves to `reserved` bucket indefinitely. `\_in\_flight\_sweeper` frees the symbol lock but does not release capital. Repeated crashes drain all available funds.

### 6.2. Paper Mode "Front-Running" Illusion

* **Location:** `zerodha\_adapter.py` (paper\_synth)
* **Risk:** Paper adapter fills limit orders after a fixed delay (`0.5s`) without checking LTP.
* **Consequence:** System "buys" at ₹100 while LTP is ₹105, generating impossible 5% paper profits. `daily\_review.py` shows inflated results, leading to overconfidence for live trading.

### 6.3. Webhook Time-Boundary Bypass

* **Location:** `webhook\_receiver.py`
* **Risk:** SHA-256 dedup uses minute-precision strings. Chartink jitter sends identical signals at `10:14:59` and `10:15:00`.
* **Consequence:** Hash changes, dedup fails. Two identical signals enter the queue concurrently, risking duplicate entries.

### 6.4. NTP Clock Stepping Poisoning

* **Location:** `time\_authority.py` ↔ `candle\_store.py`
* **Risk:** NTP steps the clock backward by 2 seconds exactly at a minute boundary.
* **Consequence:** `candle\_store` generates a candle with a timestamp older than the previous one. Downstream components crash due to non-monotonic time-series.

### 6.5. Werkzeug Development Server in Production

* **Location:** `webhook\_receiver.py`
* **Risk:** Flask’s built-in server lacks connection pooling, timeouts, and buffer limits.
* **Consequence:** Chartink sends a burst of 50 signals. Werkzeug ties up threads holding TCP sockets open. New connections are dropped. System goes deaf during high volatility.

\---

## 7\. Summary of Required Fixes (AI Priority Queue)

### Phase 1: Prevent Financial Loss \& Broker Rejection

1. **Atomic Registration (`order\_placer.py`):** Register `internal\_order\_id` with `OrderMonitor` **before** calling `adapter.place\_order()`.
2. **Naked Short Fix (`order\_protocol\_limit.py`):** Never place TGT/SL legs upfront. Wait for `OrderFilled` event on ENTRY leg, then place legs for exact `qty\_filled`.
3. **Float-Drift Fix (`fund\_manager.py`):** Convert all ledger math to `decimal.Decimal` or integer paise (₹100.50 = `10050`). Remove `float` from `CostBreakdown`.
4. **Tick-Safe Quantization (`instrument\_cache.py`):** Create `quantize\_price(price, tick\_size)` function. Wrap **every** price calculation before sending to `zerodha\_adapter`.

### Phase 2: Fix Broker API \& EOD Protocols

5. **CO Square-off (`eod\_squareoff.py`):** Detect `intent == "COVER\_ORDER"` and use `adapter.cancel\_order(variety="co")` instead of reverse `MARKET` orders.
6. **EOD Limit Protocol (`eod\_squareoff.py`):** Replace `MARKET` orders with aggressive Limit orders (`LTP ± 1%`) to cap slippage.
7. **Partial Fill Adoption (`order\_monitor.py`):** Emit `OrderPartiallyFilled` event before canceling a timed-out partial order.

### Phase 3: Concurrency \& State Safety

8. **Portfolio Lock (`risk\_engine.py` → `fund\_manager.py`):** Wrap "Check" (RiskEngine) and "Act" (Capital reserve) in a single atomic `PortfolioLock` transaction.
9. **Rehydration Fix (`main.py` → `fund\_manager.py`):** On startup, query `order\_manager.get\_open\_orders()` and rebuild `\_reserved` bucket from `PENDING\_FILL` orders.
10. **Shadow Tracker Cleanup (`shadow\_tracker.py`):** Subscribe to `EodSquareoffComplete` event to force-terminate all simulated innings at 15:30.

### Phase 4: Production Hardening

11. **Production WSGI (`webhook\_receiver.py`):** Replace `app.run()` with **Gunicorn** or **Waitress** with strict timeouts.
12. **Watchdog Timer (`live\_feed.py`):** If no tick received for `X` seconds during market hours, force WebSocket reconnect.
13. **WAL Maintenance (`state\_store.py`):** Scheduled task at 16:00 IST to execute `PRAGMA wal\_checkpoint(TRUNCATE);`.
14. **Entry Gate Rehydration (`entry\_gate.py`):** Query DB for `status == 'GATE\_WAITING'` on startup and re-add to `\_watchlist`.

