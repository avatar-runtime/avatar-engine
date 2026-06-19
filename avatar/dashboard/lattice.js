/* ============================================================================
   Tri-Node Execution Lattice — the Avatar Runtime mark.
   A minimal geometric symbol: three beveled nodes (durable execution, state,
   crash-resumable computation) bound by a lattice of edges. The apex node is
   "sealed" (mint-filled) — a committed, durable record.

   Injected into every <svg class="logo-lattice"> on the page so the mark stays
   consistent between the landing page, the dashboard, and the footer.
   ========================================================================== */
(function () {
  const MINT = "#2DD4A7";
  const LINE = "#3A416A";   // slightly brighter than --line so the mark reads at small sizes

  // Three nodes at the vertices of an upright triangle + the edges between them.
  const NODES = [
    { x: 16, y: 6,  sealed: true  },  // apex  — sealed/committed
    { x: 6,  y: 24, sealed: false },  // base left
    { x: 26, y: 24, sealed: false },  // base right
  ];
  const r = 3.4; // half-size of each node cube

  function cube(n) {
    const fill = n.sealed ? MINT : "#151A2E";
    const stroke = n.sealed ? MINT : LINE;
    // outer cube + inner bevel face
    return `
      <rect x="${n.x - r}" y="${n.y - r}" width="${r * 2}" height="${r * 2}" rx="1.2"
            fill="${fill}" stroke="${stroke}" stroke-width="1.3"/>
      <rect x="${n.x - r + 1.4}" y="${n.y - r + 1.4}" width="${(r - 1.4) * 2}" height="${(r - 1.4) * 2}" rx=".6"
            fill="none" stroke="${n.sealed ? "#06231B" : LINE}" stroke-width=".8" opacity="${n.sealed ? .5 : .6}"/>`;
  }

  function edge(a, b) {
    return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${LINE}" stroke-width="1.3" stroke-linecap="round"/>`;
  }

  const markup =
    // edges first (behind nodes) — the lattice
    edge(NODES[0], NODES[1]) +
    edge(NODES[0], NODES[2]) +
    edge(NODES[1], NODES[2]) +
    // a short stub from the apex signalling the "live" execution head
    `<line x1="16" y1="6" x2="16" y2="1.5" stroke="${MINT}" stroke-width="1.3" stroke-linecap="round"/>` +
    NODES.map(cube).join("");

  function paint() {
    document.querySelectorAll("svg.logo-lattice").forEach((svg) => {
      if (svg.dataset.painted) return;
      svg.setAttribute("viewBox", "0 0 32 32");
      svg.innerHTML = markup;
      svg.dataset.painted = "1";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", paint);
  } else {
    paint();
  }
  // Expose for dynamically-rendered headers (e.g. the dashboard SPA).
  window.AvatarLattice = { paint, markup };
})();
