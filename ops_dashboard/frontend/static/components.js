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
   mutates a page's own fields — mixin members are prefixed (t*/f*/p*/dt*). */

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
