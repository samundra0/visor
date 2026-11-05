// chat client (v1.7) — live chat bridge card.
// Each `chat` block is a card: recent transcript (from the server's
// /api/plugins/chat/status), a waiting indicator while the agent is
// running, and an input box. Send POSTs to /api/plugins/chat/send,
// which the server signs (HMAC V2) and forwards to the Hermes webhook.
// The agent's answer lands on the board as its own reply_tag block
// (pushed by the agent via visor_push) — that card arrives over SSE
// like any other push; this card just tracks the pending state.
window.VisorModules = window.VisorModules || {};

(function () {
  var _pollTimer = null;
  var _board = "main";

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function ago(ts) {
    if (!ts) return "";
    var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    return s < 60 ? s + "s" : s < 3600 ? Math.floor(s / 60) + "m" : Math.floor(s / 3600) + "h";
  }

  function fetchJ(method, url, body) {
    return fetch(url, { method: method, headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined }).then(function (r) { return r.json(); });
  }

  function renderCard(b) {
    var d = b.data || {};
    var placeholder = d.placeholder || "ask the agent…";
    return '<div class="chat-card">' +
      '<div class="chat-tx" id="cmtx-' + b.id + '"><div class="cap">loading…</div></div>' +
      '<div class="chat-wait" id="cmwait-' + b.id + '"></div>' +
      '<form class="chat-form" data-block="' + b.id + '">' +
      '<input type="text" class="chat-in" placeholder="' + esc(placeholder) + '" maxlength="2000" autocomplete="off"/>' +
      '<button type="submit">send</button></form>' +
      '<div class="cap" id="cmeta-' + b.id + '"></div>' +
      '</div>';
  }

  function renderTranscript(bid, items) {
    var el = document.getElementById("cmtx-" + bid);
    if (!el) return;
    if (!items || !items.length) {
      el.innerHTML = '<div class="cap">no messages yet — ask something</div>';
      return;
    }
    el.innerHTML = items.map(function (m) {
      var cls = m.who === "user" ? "chat-u" : m.who === "agent" ? "chat-a" : "chat-e";
      var who = m.who === "user" ? "you" : m.who;
      return '<div class="chat-row ' + cls + '"><span class="chat-w">' + who +
        (m.ts ? ' · ' + ago(m.ts) : "") + '</span><div class="chat-b">' +
        md(String(m.text || "")) + '</div></div>';
    }).join("");
  }

  // Refresh every visible chat card from the server status. Called on
  // board switch, on chat SSE ops, and on a 3s poll (waiting timer +
  // transcript tail). Cheap: one GET, no store writes.
  function refreshAll() {
    fetchJ("GET", "/api/plugins/chat/status?board=" + encodeURIComponent(_board))
      .then(function (st) {
        // clear stale waiting states for blocks that are no longer pending
        var pending = st.pending || {};
        document.querySelectorAll(".chat-wait").forEach(function (w) {
          var bid = w.id.replace("cmwait-", "");
          if (pending[bid]) {
            w.innerHTML = '<span class="chat-waiting">⏳ waiting for the agent… ' +
              ago(pending[bid].ts) + '</span>';
          } else if (w.innerHTML) {
            w.innerHTML = "";
          }
        });
        Object.keys(st.transcripts || {}).forEach(function (bid) {
          renderTranscript(bid, st.transcripts[bid]);
        });
      })
      .catch(function () { /* server hiccups: keep last render */ });
  }

  function ensurePoll() {
    if (_pollTimer) return;
    _pollTimer = setInterval(refreshAll, 3000);
  }

  function bindForms() {
    document.querySelectorAll("form.chat-form").forEach(function (form) {
      if (form.dataset.bound) return;
      form.dataset.bound = "1";
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var input = form.querySelector(".chat-in");
        var msg = input.value.trim();
        if (!msg) return;
        var bid = form.dataset.block;
        var mEl = document.getElementById("cmeta-" + bid);
        fetchJ("POST", "/api/plugins/chat/send?board=" + encodeURIComponent(_board),
          { message: msg, block: bid })
          .then(function (r) {
            if (r.sent) {
              input.value = "";
              if (mEl) mEl.innerHTML = "";
              // optimistic user row + waiting state, then server refresh
              refreshAll();
            } else if (mEl) {
              mEl.innerHTML = '<span class="cap" style="color:#dc2626">' +
                esc(r.reason || "not sent") + '</span>';
            }
          })
          .catch(function (err) {
            if (mEl) mEl.innerHTML = '<span class="cap" style="color:#dc2626">send failed: ' +
              esc(String(err && err.message || err)) + '</span>';
          });
      });
    });
  }

  let _inited = false;

  function init() {
    try {
      _board = (location.search.match(/board=([^&]+)/) || [])[1] || _board;
    } catch (e) { }
    bindForms();
    refreshAll();
    ensurePoll();
    _inited = true;
  }

  window.VisorModules.chat = {
    cardTypes: ["chat"],
    renderCard: renderCard,
    onBoard: function (bid) { _board = bid; if (_inited) refreshAll(); },
    onSSE: function (msg) { if (msg.op === "chat") refreshAll(); },
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
