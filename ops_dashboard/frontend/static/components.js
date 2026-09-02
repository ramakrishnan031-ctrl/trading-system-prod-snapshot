/* ops_dashboard/frontend/static/components.js
   G5a — shared Alpine mixins for the reusable component framework.

   Loaded SYNCHRONOUSLY before alpine.min.js (deferred) in base.html, so every
   factory below exists at Alpine init — same guarantee the inline pageBase()
   already relies on. Self-contained: NO network, NO external URLs (isolation I7
   CDN grep gate scans this file).

   Consumption pattern (screens wire these in G5b+; unchanged from the existing
   pageBase precedent):

     function ordersPage() {
       return Object.assign(
         pageBase("/api/orders"),        // load()/qs()/money()/dash()
         tableMixin({ defaultSize: 100 }),
         filterMixin([{ key: "leg" }, { key: "symbol" }]),
         { ...page-specific fields... }
       );
     }

   The macros in templates/components.html render markup that binds to these
   mixin members (tSort/tArrow/tPaged/fVals/fReset/pPeriod ...). Nothing here
   mutates a page's own fields — mixin members are prefixed (t*, f*, p*, dt*). */

/* ── DataTable: client-side sort + rows-per-page over a rows[] the page owns ── */
function tableMixin(opts) {
  opts = opts || {};
  var sizes = opts.pageSizes || [50, 100, 200, 500];
  return {
    tSortKey: opts.sortKey || "",
    tSortDir: opts.sortDir || 1,          /* 1 = asc, -1 = desc */
    tPageSize: opts.defaultSize || sizes[0],
    tPage: 1,
    tPageSizes: sizes,

    tSort: function (key) {
      if (this.tSortKey === key) { this.tSortDir = -this.tSortDir; }
      else { this.tSortKey = key; this.tSortDir = 1; }
      this.tPage = 1;
    },
    tArrow: function (key) {
      if (this.tSortKey !== key) return "↕";                 /* ↕ unsorted */
      return this.tSortDir === 1 ? "▲" : "▼";           /* ▲ / ▼ */
    },
    tSorted: function (rows) {
      rows = Array.isArray(rows) ? rows.slice() : [];
      if (!this.tSortKey) return rows;
      var k = this.tSortKey, d = this.tSortDir;
      return rows.sort(function (a, b) {
        var x = a ? a[k] : null, y = b ? b[k] : null;
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;              /* nulls last */
        if (y === null || y === undefined) return -1;
        var nx = Number(x), ny = Number(y);
        if (!Number.isNaN(nx) && !Number.isNaN(ny) && x !== "" && y !== "") return (nx - ny) * d;
        return String(x).localeCompare(String(y)) * d;
      });
    },
    tPageCount: function (rows) {
      var n = Array.isArray(rows) ? rows.length : 0;
      return Math.max(1, Math.ceil(n / this.tPageSize));
    },
    tPaged: function (rows) {
      var sorted = this.tSorted(rows);
      var pages = this.tPageCount(sorted);
      if (this.tPage > pages) this.tPage = pages;
      var start = (this.tPage - 1) * this.tPageSize;
      return sorted.slice(start, start + this.tPageSize);
    },
    tCell: function (v, kind) {
      if (v === null || v === undefined || v === "") return "—";   /* — */
      if (kind === "ts") { var s = String(v).replace("T", " "); return s.length >= 19 ? s.slice(11, 19) : s; }
      if (kind === "date") return String(v).slice(0, 10);
      if (kind === "money") { var n = Number(v); return (n < 0 ? "-₹" : "₹") + Math.abs(n).toFixed(2); }
      return v;
    },
  };
}

/* ── ColDrag: drag a heading onto a neighbour to reorder the column ──────────
   ONE implementation. This behaviour was copy-pasted into fifteen templates,
   byte-identical except for a trailing comment; that is fifteen places for the
   next fix to be applied in and fourteen for it to be missed. It lives here now.

   The consuming component owns TWO things the mixin deliberately does not:
     · `cols`      — the live column array it reorders in place
     · `saveCols()`— how (or whether) the order is persisted
   Nothing else is assumed, so a table that does NOT persist its order can pass
   a no-op saveCols and still drag.

   `_dragEndAt` exists because the same <th> is both the drag handle and the
   sort button: a drop fires a click straight after, and without the timestamp
   every reorder would also flip the sort. Headers guard with
   `Date.now() - this._dragEndAt < 250`. ⛔ Do not remove it. */
function colDragMixin(opts) {
  opts = opts || {};
  return {
    /* Column-order persistence. A page that already defines its own
       COLS_KEY/DEFAULT_COLS/initCols/saveCols KEEPS them: Object.assign puts the
       page object last, so page members win. These are the defaults for pages
       that have none, so applying the rule to a new screen adds no sixteenth
       copy of the same helpers. */
    COLS_KEY: opts.storageKey || "",
    DEFAULT_COLS: [],
    cols: [],

    initCols: function () {
      this.cols = this.DEFAULT_COLS.slice();
      if (!this.COLS_KEY) return;
      var saved = null;
      try { saved = JSON.parse(window.localStorage.getItem(this.COLS_KEY) || "null"); }
      catch (e) { saved = null; }
      if (!Array.isArray(saved) || !saved.length) return;
      /* A STALE saved order must not drop a column added later, nor resurrect
         one removed since. Rebuild from DEFAULT_COLS and only accept a full set. */
      var byKey = {};
      this.DEFAULT_COLS.forEach(function (c) { byKey[c.key] = c; });
      var next = [];
      saved.forEach(function (k) {
        if (byKey[k] && next.indexOf(byKey[k]) === -1) next.push(byKey[k]);
      });
      this.DEFAULT_COLS.forEach(function (c) {
        if (next.indexOf(c) === -1) next.push(c);
      });
      if (next.length === this.DEFAULT_COLS.length) this.cols = next;
    },
    saveCols: function () {
      if (!this.COLS_KEY) return;
      try {
        window.localStorage.setItem(this.COLS_KEY,
          JSON.stringify(this.cols.map(function (c) { return c.key; })));
      } catch (e) {}
    },
    resetCols: function () { this.cols = this.DEFAULT_COLS.slice(); this.saveCols(); },
    isDefaultOrder: function () {
      return this.cols.every(function (c, i) { return c.key === this.DEFAULT_COLS[i].key; }, this);
    },

    dragKey: "",
    overKey: "",
    _dragEndAt: 0,

    onDragStart: function (key, ev) {
      this.dragKey = key;
      if (ev && ev.dataTransfer) {
        ev.dataTransfer.effectAllowed = "move";
        try { ev.dataTransfer.setData("text/plain", key); } catch (e) {}
      }
    },
    onDrop: function (targetKey) {
      var from = this.cols.findIndex(function (c) { return c.key === this.dragKey; }, this);
      var to = this.cols.findIndex(function (c) { return c.key === targetKey; }, this);
      this.overKey = "";
      if (from < 0 || to < 0 || from === to) return;
      var next = this.cols.slice();
      next.splice(to, 0, next.splice(from, 1)[0]);
      this.cols = next;
      if (typeof this.saveCols === "function") this.saveCols();
      this._dragEndAt = Date.now();          /* suppress the sort-click that follows */
    },
    onDragEnd: function () {
      this.dragKey = "";
      this.overKey = "";
      this._dragEndAt = Date.now();
    },
  };
}

/* ── FilterBar: declarative filter values → query string ── */
function filterMixin(defs) {
  var init = {};
  (defs || []).forEach(function (d) { init[d.key] = (d && d.default) || ""; });
  return {
    fVals: init,
    fReset: function () {
      var self = this;
      Object.keys(self.fVals).forEach(function (k) { self.fVals[k] = ""; });
    },
    fQuery: function () {
      var p = new URLSearchParams();
      var self = this;
      Object.keys(self.fVals).forEach(function (k) {
        var v = self.fVals[k];
        if (v !== "" && v !== null && v !== undefined) p.set(k, v);
      });
      var s = p.toString();
      return s ? "?" + s : "";
    },
  };
}

/* ── PeriodSelector: Today/Week/Month/Custom → a range the screen passes on.
   G5a only EMITS; screens map pQuery() onto their endpoint's ?period in G5b+. ── */
function periodMixin(def) {
  return {
    pPeriod: def || "today",
    pFrom: "",
    pTo: "",
    pSet: function (period) { this.pPeriod = period; },
    pRange: function () { return { period: this.pPeriod, from: this.pFrom, to: this.pTo }; },
    pQuery: function () {
      var p = new URLSearchParams();
      p.set("period", this.pPeriod);
      if (this.pPeriod === "custom") {
        if (this.pFrom) p.set("from", this.pFrom);
        if (this.pTo) p.set("to", this.pTo);
      }
      return "?" + p.toString();
    },
  };
}
