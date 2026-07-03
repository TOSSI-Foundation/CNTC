/* upfbench dashboard — UI interactions. Portable, framework-agnostic.
   All behaviors are progressive: pages render fully without JS.
   ----------------------------------------------------------------------------
   1. Campaign switcher  : <select data-switcher> navigates on change
   2. Campaign filter    : <input data-filter-search> + <div data-filter-mode>
   3. Scorecard sticky   : .scorecard reveals .scorebar when scrolled past
   4. Suite jump links   : a[data-jump] smooth-scroll to a suite section
   5. Table sort         : th.sortable click-to-sort on .data-table[data-sortable]
   ============================================================================ */
(function () {
  "use strict";
  var onReady = function (fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  };

  /* ---- 1. campaign switcher -------------------------------------------- */
  function initSwitcher() {
    document.querySelectorAll("select[data-switcher]").forEach(function (sel) {
      sel.addEventListener("change", function () {
        if (sel.value) window.location.href = sel.value;
      });
    });
  }

  /* ---- 2. campaign list filter ---------------------------------------- */
  function initFilter() {
    // [data-filter-search] may be the input itself or a wrapper (Dash dcc.Input can't take
    // data-* attributes, so the hook sits on the .search wrapper there).
    var sb = document.querySelector("[data-filter-search]");
    var search = sb ? (sb.matches("input") ? sb : sb.querySelector("input")) : null;
    var modeBox = document.querySelector("[data-filter-mode]");
    var list = document.querySelector("[data-filter-list]");
    if (!list) return;
    var rows = Array.prototype.slice.call(list.querySelectorAll("[data-camp]"));
    var countEl = document.querySelector("[data-filter-count]");
    var emptyEl = document.querySelector("[data-filter-empty]");
    var mode = "all";

    function apply() {
      var q = (search && search.value || "").trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (r) {
        var name = (r.getAttribute("data-camp") || "").toLowerCase();
        var suites = (r.getAttribute("data-suites") || "").toLowerCase();
        var rmode = (r.getAttribute("data-mode") || "").toLowerCase();
        var okText = !q || name.indexOf(q) > -1 || suites.indexOf(q) > -1;
        var okMode = mode === "all" || rmode === mode.toLowerCase();
        var vis = okText && okMode;
        r.style.display = vis ? "" : "none";
        if (vis) shown++;
      });
      if (countEl) countEl.textContent = shown + " / " + rows.length;
      if (emptyEl) emptyEl.style.display = shown ? "none" : "block";
    }
    if (search) search.addEventListener("input", apply);
    if (modeBox) {
      modeBox.querySelectorAll("button[data-mode]").forEach(function (b) {
        b.addEventListener("click", function () {
          modeBox.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
          b.classList.add("on");
          mode = b.getAttribute("data-mode");
          apply();
        });
      });
    }
    apply();
  }

  /* ---- 3. scorecard -> sticky scorebar -------------------------------- */
  function initScorebar() {
    var card = document.querySelector(".scorecard");
    var bar = document.querySelector(".scorebar");
    if (!card || !bar) return;
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          bar.classList.toggle("show", !e.isIntersecting && e.boundingClientRect.top < 0);
        });
      }, { rootMargin: "-8px 0px 0px 0px", threshold: 0 });
      io.observe(card);
    }
  }

  /* ---- 4. suite jump links -------------------------------------------- */
  function scrollToEl(el) {
    if (!el) return;
    var y = el.getBoundingClientRect().top + window.scrollY - 64;
    window.scrollTo({ top: y, behavior: "smooth" });
  }
  function initJump() {
    document.querySelectorAll("a[data-jump]").forEach(function (a) {
      a.addEventListener("click", function (ev) {
        var id = a.getAttribute("href");
        if (id && id.charAt(0) === "#") {
          ev.preventDefault();
          scrollToEl(document.querySelector(id));
        }
      });
    });
  }

  /* ---- 5. sortable tables --------------------------------------------- */
  function initSort() {
    document.querySelectorAll("table.data-table[data-sortable]").forEach(function (tbl) {
      var ths = tbl.querySelectorAll("thead th");
      ths.forEach(function (th, idx) {
        if (th.classList.contains("nosort")) return;
        th.classList.add("sortable");
        if (!th.querySelector(".arr")) {
          var s = document.createElement("span"); s.className = "arr"; s.textContent = "▾";
          th.appendChild(s);
        }
        th.addEventListener("click", function () {
          var body = tbl.tBodies[0];
          var rows = Array.prototype.slice.call(body.rows);
          var asc = !th.classList.contains("asc");
          ths.forEach(function (h) { h.classList.remove("asc", "desc"); });
          th.classList.add(asc ? "asc" : "desc");
          rows.sort(function (a, b) {
            var x = a.cells[idx] ? a.cells[idx].textContent.trim() : "";
            var y = b.cells[idx] ? b.cells[idx].textContent.trim() : "";
            var nx = parseFloat(x.replace(/[^0-9.eE+-]/g, ""));
            var ny = parseFloat(y.replace(/[^0-9.eE+-]/g, ""));
            var both = !isNaN(nx) && !isNaN(ny);
            var cmp = both ? nx - ny : x.localeCompare(y);
            return asc ? cmp : -cmp;
          });
          rows.forEach(function (r) { body.appendChild(r); });
        });
      });
    });
  }

  // Re-runnable so the Dash port can call it after React swaps the page content.
  window.upfbenchInit = function () {
    try { initSwitcher(); } catch (e) {}
    try { initFilter(); } catch (e) {}
    try { initScorebar(); } catch (e) {}
    try { initJump(); } catch (e) {}
    try { initSort(); } catch (e) {}
  };
  onReady(window.upfbenchInit);
})();
