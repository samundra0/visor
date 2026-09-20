// live client (v1.6) — renders auto-refreshing blocks.
// The server module polls the URL and writes data.live = {value, ts, error,
// fails, history} back into the block; each refresh emits an upsert, so this
// card re-renders in place with fresh data + a fresh "updated Xs ago" footer.
// Helpers are module-scoped closures (not `this`-bound methods) so renderCard
// works no matter how the core invokes it (spread copies drop `this`).
window.VisorModules = window.VisorModules || {};

(function () {
  function ago(ts) {
    if (!ts) return "never";
    var s = Math.max(0, Math.round((Date.now() / 1000) - ts));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    return Math.floor(s / 3600) + "h ago";
  }

  function spark(hist) {
    var nums = hist.map(Number).filter(function (n) { return isFinite(n); });
    if (nums.length < 2) return '<div class="cap">collecting…</div>';
    var w = 340, h = 64, pad = 4;
    var min = Math.min.apply(null, nums), max = Math.max.apply(null, nums);
    var span = (max - min) || 1;
    var pts = nums.map(function (n, i) {
      var x = pad + i / (nums.length - 1) * (w - 2 * pad);
      var y = h - pad - (n - min) / span * (h - 2 * pad);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var last = nums[nums.length - 1];
    var ly = h - pad - (last - min) / span * (h - 2 * pad);
    return '<svg class="spark" width="100%" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      '<polyline points="' + pts + '" fill="none" stroke="#4f46e5" stroke-width="2"/>' +
      '<circle cx="' + (w - pad) + '" cy="' + ly.toFixed(1) + '" r="3" fill="#4f46e5"/></svg>';
  }

  function fmt(v) {
    if (v == null) return "—";
    if (typeof v === "number") return isFinite(v) ? String(Math.round(v * 100) / 100) : String(v);
    var s = String(v);
    return s.length > 120 ? s.slice(0, 117) + "…" : s;
  }

  function renderCard(b) {
    var d = b.data || {};
    var lv = d.live || {};
    var kind = d.as || "stat";
    var fails = lv.fails || 0;
    var dim = fails >= 10;
    var html = "";
    if (lv.error && kind === "stat") {
      html += '<div class="cap" style="color:#dc2626">⚠ ' + esc(lv.error) +
        (fails > 1 ? " ×" + fails : "") + "</div>";
    }
    if (kind === "spark") {
      html += spark(lv.history || []);
    } else if (kind === "table" && Array.isArray(lv.value)) {
      var rows = lv.value.slice(0, 30);
      if (rows.length && typeof rows[0] === "object" && !Array.isArray(rows[0])) {
        var keys = Object.keys(rows[0]);
        html += '<table class="tb"><thead><tr>' +
          keys.map(function (k) { return "<th>" + esc(k) + "</th>"; }).join("") +
          "</tr></thead><tbody>" +
          rows.map(function (r) {
            return "<tr>" + keys.map(function (k) {
              return "<td>" + md(String(r[k] == null ? "" : r[k])) + "</td>";
            }).join("") + "</tr>";
          }).join("") + "</tbody></table>";
      } else {
        html += rows.length
          ? '<div class="md">' + rows.map(function (r) { return "- " + md(String(r)); }).join("<br>") + "</div>"
          : '<div class="cap">empty</div>';
      }
    } else if (kind === "text") {
      var raw;
      if (lv.value == null) raw = "";
      else if (typeof lv.value === "object") raw = JSON.stringify(lv.value, null, 1);
      else raw = String(lv.value);
      html += typeof lv.value === "object"
        ? '<pre class="code"><code>' + esc(raw) + '</code></pre>'
        : '<div class="md">' + md(raw) + "</div>";
    } else {
      // stat (default): value + delta from previous history point
      var histNums = (lv.history || []).map(Number).filter(function (n) { return isFinite(n); });
      var cur = histNums[histNums.length - 1];
      var prev = histNums.length > 1 ? histNums[histNums.length - 2] : null;
      var delta = (cur != null && prev != null) ? cur - prev : null;
      var tone = delta == null ? "cy" : delta > 0 ? "ok" : delta < 0 ? "warn" : "cy";
      html += '<div class="stat"><div class="v ' + tone + '">' +
        (cur != null ? esc(String(Math.round(cur * 100) / 100)) : esc(fmt(lv.value))) +
        (delta != null ? '<div class="s">' + (delta > 0 ? "▲ +" : delta < 0 ? "▼ " : "") +
          esc(String(Math.round(delta * 100) / 100)) + "</div>" : "") +
        '</div><div class="l">live</div></div>';
    }
    var dot = lv.error ? '<span style="color:#dc2626">●</span>' : '<span style="color:#10b981">●</span>';
    html += '<div class="cap" style="margin-top:8px;opacity:' + (dim ? "0.55" : "1") + '">' +
      dot + " updated " + ago(lv.ts) +
      (d.url ? ' · ' + esc(d.url) : "") +
      (d.interval ? " · every " + d.interval + "s" : "") + "</div>";
    return html;
  }

  window.VisorModules.live = { cardTypes: ["live"], renderCard: renderCard };
})();
