# S06 POSITIONS — CORRECTED RE-RENDER · 24-Aug-2026 ~14:2x IST

# 👤 RAMA — THIS IS THE ARTEFACT YOUR CONFIRMATION IS WAITING ON.

⛔ **This is NOT an approval and this pass did not treat it as one.** Your *"Screen approved"* of
20-Aug **23:3x** was given on the **PRE-correction** render; the grouped-band fix (`b47e148`) landed
**after** it. The ledger gated S07 on you seeing the corrected render. **This is that render.**

| capture | viewport |
|---|---|
| `S06_positions_1896x988.png` | **1896 × 988** — the wider of the two approved viewports |
| `S06_positions_1416x808.png` | **1416 × 808** |

---

## ⭐ WHAT TO LOOK AT — the four grouped bands

Scroll to the table. The four bands you asked about now have a **1px vertical rule at every band
boundary**, carried down the group row, the sub-heading row **and** the body rows, so a band stays
traceable all the way down:

| band | sub-headings |
|---|---|
| **QTY** | System · Position |
| **ENTRY ₹** | System · Filled |
| **SL ₹** | System · Broker |
| **TGT ₹** | System · Broker |

⭐ **The rule reuses the group underline's own token `var(--card-bd)`** — ⛔ no new colour, ⛔ no new
visual concept, ⛔ no extra column width. Your sample's coloured boxes were an annotation, and you
marked it *"not binding as it is"*.
⭐ **It follows a drag.** The separator is computed from the CURRENT column order, ⛔ never
`nth-child`, so re-ordering columns carries the boundary with the band.

## 🔬 MEASURED — both viewports

| check | 1896×988 | 1416×808 |
|---|---|---|
| separators on the GROUP row | **5** | **5** |
| separators on the SUB-HEADING row | **5** | **5** |
| separators on each of the 7 data rows | **5** | **5** |
| page overflow-x | **0** | **0** |
| clipped headers | **0** | **0** |
| clipped data cells | **0** | **0** |
| console errors | **0** | — |

5 = the four band edges **plus** the close after `TGT ₹`.

## 🔬 THE DATA IS REAL — ⛔ not invented

7 **real** rows pulled **read-only** from the production VM for **2026-08-24**: 2 **Delivery** rows
(BALUFORGE, KAMATHOTEL) carrying genuine broker SL/TGT, and 5 **Intraday** rows whose broker cells
are NULL — so the honest `—` is **exercised**, ⛔ not assumed. The KPI figures are the reader's own
queries run against the live DB: **OPEN 1 · LONG 1 · SHORT 0 · CAPITAL USED ₹445.54 · REALIZED
₹31.24**.
⭐ **CURRENT MTM deliberately reads `n/a — Pending Broker Source`**: it needs a live price and the
GUI has none. ⛔ A number there would be the one lie this screen is built not to tell.

## ⚠️ TWO THINGS IN THE CAPTURE THAT ARE THE HARNESS, ⛔ NOT THE SCREEN

1. **`BROKER ID` and `CLIENT NAME` in the header show `——`.** The PC's config points the roster and
   DBs at the **VM** path, so a local render cannot resolve them. On the VM they populate normally.
2. **`TRADER` reads `DOWN`.** Same cause — the local harness has no service to report on.

⛔ **Neither is a regression, and neither was "fixed" for the screenshot.**

## ⛔ WHAT WAS **NOT** DONE

⛔ No S06 code, CSS or test was modified — the instruction was to change nothing unless the
re-render proved a real defect, and it proved none.
⛔ S07 Trade Explorer is **NOT started** — it stays gated on your confirmation of this render.
⛔ S08 Capital & Risk is **ON HOLD** and was not touched.
⛔ **PUSH = NO · DEPLOY = NO.** The GUI branch `feat/screen10-slippage-analytics` stays local.

## 👤 WHAT IS NEEDED FROM YOU

**One look, and one word.** If the bands now read clearly, S06 becomes visually confirmed and S07
unlocks. If anything is still off, say what and it gets corrected before S07 starts.
