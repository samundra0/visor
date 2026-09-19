// selftest client — the canonical minimal visor module card.
// Contract: register under window.VisorModules.<name>; the core calls
// renderCard(b) for blocks whose type is in cardTypes. Returned HTML is
// inserted via innerHTML; <script> tags inside it ARE re-executed by the
// core (makeEl), so interactive modules work.
window.VisorModules = window.VisorModules || {};
window.VisorModules.selftest = {
  cardTypes: ["selftest"],
  renderCard: function (b) {
    var d = (b.data && b.data.html) || "";
    var touched = b.data && b.data.touched ? "touched by transform" : "transform did not run";
    return '<div class="md">' + (d ? "<p>" + d + "</p>" : "") +
      '<div class="cap">' + touched + " · board " +
      (b.data && b.data.board ? b.data.board : "?") + "</div></div>";
  },
};
