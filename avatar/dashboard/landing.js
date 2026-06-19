/* ============================================================================
   Avatar Runtime — landing page motion engine.
   Pure vanilla JS building SVG/DOM. No build step, no dependencies.
   Motion vocabulary: commit pulse · lease glow · crash flicker · replay sweep
   · fork split. Honors prefers-reduced-motion (handled in CSS).
   ========================================================================== */
(function () {
  "use strict";
  const SVGNS = "http://www.w3.org/2000/svg";
  const MINT = "#2DD4A7", CRASH = "#F0506A", WARN = "#E5B567",
        LINE = "#262C49", SLATE = "#1B2138", MUTED = "#8A93AD", PAPER = "#EDEFF7";

  // tiny SVG element helper
  function el(tag, attrs, kids) {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in (attrs || {})) n.setAttribute(k, attrs[k]);
    (kids || []).forEach((c) => n.appendChild(c));
    return n;
  }
  // a beveled volumetric node-cube as a <g> centered at 0,0
  function cube(size, color, glyph, opts) {
    opts = opts || {};
    const h = size / 2;
    const g = el("g", { class: "n-cube" });
    g.appendChild(el("rect", {
      x: -h, y: -h, width: size, height: size, rx: size * 0.16,
      fill: opts.fill || SLATE, stroke: color, "stroke-width": 1.6,
    }));
    g.appendChild(el("rect", {
      x: -h + 4, y: -h + 4, width: size - 8, height: size - 8, rx: size * 0.1,
      fill: "none", stroke: color, "stroke-width": 1, opacity: 0.3,
    }));
    if (glyph) {
      const t = el("text", {
        x: 0, y: 0, "text-anchor": "middle", "dominant-baseline": "central",
        fill: opts.glyphColor || color, "font-family": "JetBrains Mono, monospace",
        "font-size": opts.fontSize || 12,
      });
      t.textContent = glyph;
      g.appendChild(t);
    }
    return g;
  }
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* =========================================================================
     1) HERO STAGE — cube cluster → graph expansion → Postgres table → replay
     ====================================================================== */
  function hero() {
    const svg = document.getElementById("heroSvg");
    if (!svg) return;
    const label = document.getElementById("phaseLabel");
    const dots = [...document.querySelectorAll("#stageDots i")];

    // node positions per phase (viewBox 400x400)
    const POS = {
      cluster: [[176, 176], [224, 176], [176, 224], [224, 224]],
      graph:   [[200, 78],  [86, 214],  [314, 214], [200, 322]],
      pg:      [[150, 96],  [150, 162], [150, 228], [150, 294]],
      replay:  [[200, 78],  [86, 214],  [314, 214], [200, 322]],
    };
    const PHASES = ["cluster", "graph", "pg", "replay"];
    const LABELS = ["cube cluster", "graph expansion", "Postgres table", "replay rewind"];

    // build 4 node groups
    const nodes = POS.cluster.map((p, i) =>
      cube(46, i === 0 ? MINT : LINE, ["▣", "◇", "◆", "◈"][i],
           { glyphColor: i === 0 ? MINT : MUTED }));
    nodes.forEach((g) => { g.style.transition = "transform .9s cubic-bezier(.22,1,.36,1)"; });

    // edges (visible only in graph/replay)
    const EDGES = [[0, 1], [0, 2], [1, 3], [2, 3], [0, 3]];
    const edges = EDGES.map(() => el("line", {
      stroke: MINT, "stroke-width": 1.6, opacity: 0,
      "stroke-linecap": "round", style: "transition:opacity .6s",
    }));
    function placeEdges(phase) {
      const P = POS[phase];
      EDGES.forEach(([a, b], i) => {
        edges[i].setAttribute("x1", P[a][0]); edges[i].setAttribute("y1", P[a][1]);
        edges[i].setAttribute("x2", P[b][0]); edges[i].setAttribute("y2", P[b][1]);
      });
    }

    // Postgres table overlay (column of rows)
    const table = el("g", { opacity: 0, style: "transition:opacity .6s" });
    for (let r = 0; r < 4; r++) {
      table.appendChild(el("rect", {
        x: 190, y: 78 + r * 66, width: 150, height: 44, rx: 6,
        fill: "none", stroke: LINE, "stroke-width": 1,
      }));
      const tx = el("text", {
        x: 200, y: 100 + r * 66, fill: MUTED, "font-family": "JetBrains Mono, monospace", "font-size": 11,
      });
      tx.textContent = ["plan", "tool_call", "observation", "final"][r];
      table.appendChild(tx);
    }
    const header = el("text", {
      x: 150, y: 56, "text-anchor": "middle", fill: MUTED,
      "font-family": "JetBrains Mono, monospace", "font-size": 10, opacity: 0,
      style: "transition:opacity .6s", "letter-spacing": "1.5",
    });
    header.textContent = "run_steps";

    // replay sweep bar
    const sweep = el("rect", { x: -60, y: 40, width: 50, height: 320, fill: MINT, opacity: 0.14, rx: 6 });

    edges.forEach((e) => svg.appendChild(e));
    svg.appendChild(table); svg.appendChild(header);
    nodes.forEach((g) => svg.appendChild(g));
    svg.appendChild(sweep);

    function apply(phase) {
      const P = POS[phase];
      nodes.forEach((g, i) => { g.setAttribute("transform", `translate(${P[i][0]},${P[i][1]})`); });
      const showEdges = (phase === "graph" || phase === "replay");
      if (showEdges) placeEdges(phase);
      edges.forEach((e) => e.setAttribute("opacity", showEdges ? 0.55 : 0));
      table.setAttribute("opacity", phase === "pg" ? 1 : 0);
      header.setAttribute("opacity", phase === "pg" ? 1 : 0);
      // recolor nodes: in pg align to ledger types, else apex sealed only
      nodes.forEach((g, i) => {
        const stroke = (phase === "pg")
          ? [ "#9C8CF0", WARN, MINT, MINT ][i]
          : (i === 0 ? MINT : LINE);
        g.querySelectorAll("rect").forEach((rc) => rc.setAttribute("stroke", stroke));
      });
      if (phase === "replay" && !reduced) runSweep();
    }

    function runSweep() {
      sweep.setAttribute("opacity", 0.16);
      const t0 = performance.now(), dur = 1600;
      (function step(t) {
        const k = Math.min(1, (t - t0) / dur);
        sweep.setAttribute("x", -60 + k * 460);
        // light nodes as the sweep passes
        nodes.forEach((g, i) => {
          const nx = POS.replay[i][0];
          const lit = (-60 + k * 460) > nx - 30;
          g.querySelectorAll("rect").forEach((rc) => rc.setAttribute("stroke", lit ? MINT : LINE));
        });
        if (k < 1) requestAnimationFrame(step); else sweep.setAttribute("opacity", 0);
      })(t0);
    }

    apply("cluster");
    if (reduced) { apply("graph"); return; }

    let i = 0;
    setInterval(() => {
      i = (i + 1) % PHASES.length;
      apply(PHASES[i]);
      label.textContent = LABELS[i];
      dots.forEach((d, k) => d.classList.toggle("on", k === i));
    }, 2800);
  }

  /* =========================================================================
     2) PROBLEM — broken vs reconstructed timelines (DOM node components)
     ====================================================================== */
  function timelines() {
    const broken = document.getElementById("brokenTl");
    const fixed = document.getElementById("fixedTl");
    if (!broken || !fixed) return;

    function node(cls, glyph, cap) {
      const w = document.createElement("div");
      w.className = "tl-node";
      const n = document.createElement("div");
      n.className = "node " + cls;
      n.style.width = n.style.height = "40px";
      n.innerHTML = `<span class="glyph">${glyph}</span>`;
      const c = document.createElement("div");
      c.className = "cap"; c.textContent = cap;
      w.appendChild(n); w.appendChild(c);
      return w;
    }
    function edge(cls) {
      const e = document.createElement("div");
      e.className = "edge " + (cls || "");
      e.style.minWidth = "20px"; e.style.alignSelf = "flex-start"; e.style.marginTop = "20px";
      return e;
    }

    broken.appendChild(node("", "1", "plan"));
    broken.appendChild(edge());
    broken.appendChild(node("", "2", "refund"));
    broken.appendChild(edge("crashed"));
    broken.appendChild(node("is-crashed", "✕", "crash"));
    broken.appendChild(edge("crashed"));
    broken.appendChild(node("", "?", "lost"));

    fixed.appendChild(node("", "1", "plan"));
    fixed.appendChild(edge("live"));
    fixed.appendChild(node("is-active", "2", "refund"));
    fixed.appendChild(edge("live"));
    fixed.appendChild(node("", "3", "resume"));
    fixed.appendChild(edge("live"));
    fixed.appendChild(node("is-sealed", "✓", "final"));
  }

  /* =========================================================================
     3) CRASH SAFETY DEMO — animated dispatch/crash/resume sequence
     ====================================================================== */
  function crashDemo() {
    const svg = document.getElementById("crashSvg");
    if (!svg) return;
    const caption = document.getElementById("crashCaption");
    const btn = document.getElementById("crashReplay");

    const STEPS = [
      { x: 70,  label: "plan" },
      { x: 200, label: "lookup" },
      { x: 330, label: "refund", crashAfter: true },
      { x: 460, label: "observe" },
      { x: 590, label: "email" },
      { x: 690, label: "final", sealed: true },
    ];
    let nodes = [], worker, worker2, timers = [];

    function build() {
      svg.innerHTML = "";
      // baseline edges
      for (let i = 0; i < STEPS.length - 1; i++) {
        svg.appendChild(el("line", {
          x1: STEPS[i].x, y1: 110, x2: STEPS[i + 1].x, y2: 110,
          stroke: LINE, "stroke-width": 2, class: `c-edge e${i}`,
        }));
      }
      nodes = STEPS.map((s) => {
        const g = cube(46, LINE, "", { });
        g.setAttribute("transform", `translate(${s.x},110)`);
        const lab = el("text", {
          x: s.x, y: 158, "text-anchor": "middle", fill: MUTED,
          "font-family": "JetBrains Mono, monospace", "font-size": 11,
        });
        lab.textContent = s.label;
        svg.appendChild(g); svg.appendChild(lab);
        return g;
      });
      // worker token
      worker = el("g", { opacity: 0 });
      worker.appendChild(el("circle", { r: 9, fill: MINT, opacity: .9 }));
      worker.appendChild(el("circle", { r: 15, fill: "none", stroke: MINT, "stroke-width": 1, opacity: .4 }));
      const wl = el("text", { x: 0, y: 30, "text-anchor": "middle", fill: MINT, "font-family": "JetBrains Mono, monospace", "font-size": 9 });
      wl.textContent = "w1"; worker.appendChild(wl);
      svg.appendChild(worker);
    }

    function lite(i, color) {
      nodes[i].querySelectorAll("rect").forEach((r) => r.setAttribute("stroke", color));
    }
    function moveWorker(w, x, cb, dur) {
      w.setAttribute("opacity", 1);
      const from = +(w.dataset.x || x), t0 = performance.now();
      dur = dur || 600;
      (function s(t) {
        const k = Math.min(1, (t - t0) / dur);
        const cx = from + (x - from) * (k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
        w.setAttribute("transform", `translate(${cx},110)`);
        if (k < 1) requestAnimationFrame(s); else { w.dataset.x = x; cb && cb(); }
      })(t0);
    }
    const wait = (ms) => new Promise((r) => timers.push(setTimeout(r, ms)));

    async function run() {
      timers.forEach(clearTimeout); timers = [];
      build();
      caption.innerHTML = "Worker <b>w1</b> leases the run and walks the ledger…";
      worker.dataset.x = 70;
      // walk first three steps
      for (let i = 0; i < 3; i++) {
        await wait(reduced ? 0 : 500);
        lite(i, MINT);
        svg.querySelector(`.e${i - 1}`) && svg.querySelector(`.e${i - 1}`).setAttribute("stroke", MINT);
        if (i < 2) await new Promise((res) => moveWorker(worker, STEPS[i + 1].x, res));
      }
      // dispatch refund (#3)
      caption.innerHTML = "<b style='color:#E5B567'>tool_call</b> issue_refund dispatched (attempt 1)…";
      lite(2, WARN);
      await wait(reduced ? 0 : 700);
      // CRASH
      caption.innerHTML = "<b style='color:#F0506A'>✕ w1 crashed</b> after dispatch, before the observation committed.";
      lite(2, CRASH);
      worker.querySelector("circle").setAttribute("fill", CRASH);
      worker.classList.add("crash-flicker");
      await wait(reduced ? 0 : 700);
      worker.setAttribute("opacity", 0);
      // resume with w2
      caption.innerHTML = "Lease expired. <b style='color:#2DD4A7'>w2</b> re-leases and rebuilds state from the ledger.";
      worker2 = el("g", { opacity: 0 });
      worker2.appendChild(el("circle", { r: 9, fill: MINT }));
      worker2.appendChild(el("circle", { r: 15, fill: "none", stroke: MINT, "stroke-width": 1, opacity: .4 }));
      const wl = el("text", { x: 0, y: 30, "text-anchor": "middle", fill: MINT, "font-family": "JetBrains Mono, monospace", "font-size": 9 });
      wl.textContent = "w2"; worker2.appendChild(wl);
      svg.appendChild(worker2);
      worker2.dataset.x = STEPS[1].x;
      await new Promise((res) => moveWorker(worker2, STEPS[2].x, res, 500));
      caption.innerHTML = "Re-dispatched with the <b>same idempotency key</b> → downstream <b style='color:#2DD4A7'>dedupes</b>.";
      lite(2, MINT);
      await wait(reduced ? 0 : 600);
      // finish remaining steps
      for (let i = 3; i < STEPS.length; i++) {
        svg.querySelector(`.e${i - 1}`) && svg.querySelector(`.e${i - 1}`).setAttribute("stroke", MINT);
        await new Promise((res) => moveWorker(worker2, STEPS[i].x, res, 480));
        lite(i, MINT);
        await wait(reduced ? 0 : 220);
      }
      // seal final
      nodes[STEPS.length - 1].querySelectorAll("rect").forEach((r) => r.setAttribute("stroke", MINT));
      caption.innerHTML = "Run <b style='color:#2DD4A7'>succeeded</b>. Dispatched twice — <b>effect happened once.</b>";
    }

    build();
    btn && (btn.onclick = run);
    // auto-run when scrolled into view (once)
    if ("IntersectionObserver" in window && !reduced) {
      const io = new IntersectionObserver((es) => {
        es.forEach((e) => { if (e.isIntersecting) { run(); io.disconnect(); } });
      }, { threshold: 0.4 });
      io.observe(svg);
    }
  }

  /* =========================================================================
     4) GUARANTEES — five nodes, hover reveals SQL-level proof
     ====================================================================== */
  function guarantees() {
    const grid = document.getElementById("guaranteeGrid");
    if (!grid) return;
    const G = [
      { t: "Single active owner", sql: "UPDATE runs SET lease_owner=$w,\n  lease_expires_at=now()+ival\nWHERE id=$id AND status='queued'\nFOR UPDATE SKIP LOCKED;" },
      { t: "No split-brain writes", sql: "-- every commit is guarded by the lease\nWHERE id=$id\n  AND lease_owner=$w\n  AND lease_expires_at > now();" },
      { t: "Deterministic replay", sql: "-- state is a pure fold of the ledger\nSELECT * FROM run_steps\nWHERE run_id=$id\nORDER BY seq;  -- append-only" },
      { t: "Crash-resume correctness", sql: "-- expired lease ⇒ reclaimable\nWHERE status IN('leased','running')\n  AND lease_expires_at < now();" },
      { t: "Idempotent tool execution", sql: "UNIQUE (run_id, idempotency_key)\n-- a dispatched-but-unobserved call\n-- re-dispatches with the SAME key" },
    ];
    G.forEach((g) => {
      const d = document.createElement("div");
      d.className = "g-node"; d.tabIndex = 0;
      d.innerHTML = `
        <div class="gtitle">
          <span class="node is-sealed" style="width:30px;height:30px;border-radius:7px;">
            <span class="glyph" style="font-size:13px">✓</span>
          </span>
          ${g.t}
        </div>
        <div class="hint">hover for SQL proof</div>
        <div class="proof"><pre class="code mono">${g.sql.replace(/</g, "&lt;")}</pre></div>`;
      grid.appendChild(d);
    });
  }

  /* =========================================================================
     5) DEVELOPER EXPERIENCE — SDK code + live-growing execution graph
     ====================================================================== */
  function dx() {
    const code = document.getElementById("dxCode");
    if (code) {
      code.innerHTML =
`<span class="tok-key">from</span> avatar <span class="tok-key">import</span> Avatar, tool, Plan, ToolCall

app = <span class="tok-fn">Avatar</span>(api_url=<span class="tok-str">"http://localhost:8088"</span>)

<span class="tok-key">@tool</span>(timeout=<span class="tok-num">10</span>, retries=<span class="tok-num">2</span>)
<span class="tok-key">def</span> <span class="tok-fn">issue_refund</span>(order_id, cents):
    <span class="tok-comment"># forward current_idempotency_key()</span>
    <span class="tok-comment"># for exactly-once end-to-end</span>
    <span class="tok-key">return</span> {<span class="tok-str">"refunded"</span>: <span class="tok-key">True</span>}

<span class="tok-key">@app.agent</span>(<span class="tok-str">"support-resolver"</span>)
<span class="tok-key">def</span> <span class="tok-fn">resolve</span>(state):
    <span class="tok-key">if</span> state.has_tool_result:
        <span class="tok-key">return</span> Plan(final=<span class="tok-key">True</span>)
    <span class="tok-key">return</span> Plan(tool_calls=[
        <span class="tok-fn">ToolCall</span>(<span class="tok-str">"c1"</span>, <span class="tok-str">"issue_refund"</span>,
                 {<span class="tok-str">"order_id"</span>: <span class="tok-str">"42"</span>})])

run = app.runs.<span class="tok-fn">create</span>(<span class="tok-str">"support-resolver"</span>)
app.runs.<span class="tok-fn">wait</span>(run.id)  <span class="tok-comment"># durable</span>`;
    }
    const svg = document.getElementById("dxSvg");
    if (!svg) return;
    const SEQ = [
      { x: 60,  y: 40,  g: "plan",  c: "#9C8CF0" },
      { x: 180, y: 90,  g: "call",  c: WARN },
      { x: 300, y: 40,  g: "obs",   c: MINT },
      { x: 180, y: 160, g: "plan",  c: "#9C8CF0" },
      { x: 300, y: 210, g: "✓",     c: MINT, sealed: true },
    ];
    function grow() {
      svg.innerHTML = "";
      SEQ.forEach((s, i) => {
        setTimeout(() => {
          if (i > 0) {
            const p = SEQ[i - 1];
            const ln = el("line", { x1: p.x, y1: p.y, x2: p.x, y2: p.y, stroke: MINT, "stroke-width": 1.6, opacity: .5 });
            svg.appendChild(ln);
            const t0 = performance.now();
            (function a(t) { const k = Math.min(1, (t - t0) / 350);
              ln.setAttribute("x2", p.x + (s.x - p.x) * k); ln.setAttribute("y2", p.y + (s.y - p.y) * k);
              if (k < 1) requestAnimationFrame(a); })(t0);
          }
          const g = cube(38, s.c, s.g === "✓" ? "✓" : "", { glyphColor: s.c });
          g.setAttribute("transform", `translate(${s.x},${s.y})`);
          g.classList.add("commit-pulse");
          const lab = el("text", { x: s.x, y: s.y + 32, "text-anchor": "middle", fill: MUTED, "font-family": "JetBrains Mono, monospace", "font-size": 10 });
          lab.textContent = s.g === "✓" ? "final" : s.g;
          svg.appendChild(g); svg.appendChild(lab);
        }, reduced ? 0 : i * 600);
      });
    }
    grow();
    if (!reduced) setInterval(grow, SEQ.length * 600 + 2400);
  }

  /* =========================================================================
     6) DASHBOARD PREVIEW — static run graph (with crash marker + fork) + SSE
     ====================================================================== */
  function dashboard() {
    const svg = document.getElementById("dashSvg");
    if (svg) {
      const N = [
        { x: 50,  y: 60,  c: "#9C8CF0", t: "#1" },
        { x: 150, y: 60,  c: WARN,      t: "#2" },
        { x: 250, y: 60,  c: CRASH,     t: "✕", crash: true },
        { x: 250, y: 150, c: MINT,      t: "#3" },      // resume (drop down = new attempt)
        { x: 350, y: 150, c: MINT,      t: "#4" },
        { x: 450, y: 150, c: MINT,      t: "✓", sealed: true },
        { x: 350, y: 230, c: MINT,      t: "f1", fork: true }, // fork branch
      ];
      const E = [[0, 1], [1, 2], [2, 3, "crash"], [3, 4], [4, 5], [4, 6, "fork"]];
      E.forEach(([a, b, kind]) => {
        svg.appendChild(el("line", {
          x1: N[a].x, y1: N[a].y, x2: N[b].x, y2: N[b].y,
          stroke: kind === "crash" ? CRASH : kind === "fork" ? MINT : LINE,
          "stroke-width": 2, "stroke-dasharray": kind ? "5 4" : "0",
          opacity: kind === "fork" ? .6 : 1,
        }));
      });
      N.forEach((n) => {
        const g = cube(34, n.c, n.crash ? "✕" : n.sealed ? "✓" : "", { glyphColor: n.c });
        g.setAttribute("transform", `translate(${n.x},${n.y})`);
        if (n.sealed) g.querySelectorAll("rect")[0].setAttribute("fill", "rgba(45,212,167,.08)");
        const lab = el("text", { x: n.x, y: n.y + 28, "text-anchor": "middle", fill: MUTED, "font-family": "JetBrains Mono, monospace", "font-size": 9 });
        lab.textContent = n.t;
        svg.appendChild(g); svg.appendChild(lab);
      });
      // resume marker
      const rm = el("text", { x: 250, y: 110, "text-anchor": "middle", fill: WARN, "font-family": "JetBrains Mono, monospace", "font-size": 9 });
      rm.textContent = "▸ resumed (attempt 2)";
      svg.appendChild(rm);
    }

    const feed = document.getElementById("sseFeed");
    if (feed) {
      const EVENTS = [
        ["tool_call", "issued", WARN],
        ["step", "committed", MINT],
        ["worker", "crashed", CRASH],
        ["run", "resumed", MINT],
        ["step", "committed", MINT],
        ["run", "succeeded", MINT],
      ];
      let i = 0;
      function tick() {
        const [a, b, c] = EVENTS[i % EVENTS.length];
        const row = document.createElement("div");
        row.style.opacity = 0; row.style.transition = "opacity .4s";
        row.innerHTML = `<span style="color:${c}">●</span> ${a} <span class="muted">${b}</span>`;
        feed.prepend(row);
        requestAnimationFrame(() => (row.style.opacity = 1));
        while (feed.children.length > 5) feed.lastChild.remove();
        i++;
      }
      tick();
      if (!reduced) setInterval(tick, 1400);
    }
  }

  /* =========================================================================
     7) CRASH SLIDER — scrub the ledger, reconstruct graph state deterministically
     ====================================================================== */
  function crashSlider() {
    const track = document.getElementById("scrubTrack");
    const slider = document.getElementById("crashSlider");
    const out = document.getElementById("scrubReadout");
    if (!track || !slider) return;

    const LEDGER = [
      { t: "plan",        c: "#9C8CF0" },
      { t: "tool_call",   c: WARN },
      { t: "observation", c: MINT },
      { t: "plan",        c: "#9C8CF0" },
      { t: "tool_call",   c: WARN },
      { t: "✕ crash",     c: CRASH },
      { t: "resume",      c: MINT },
      { t: "observation", c: MINT },
      { t: "plan",        c: "#9C8CF0" },
      { t: "tool_call",   c: WARN },
      { t: "final ✓",     c: MINT },
    ];
    slider.max = LEDGER.length - 1;
    const chips = LEDGER.map((s, i) => {
      const w = document.createElement("div");
      w.style.cssText = "display:flex;flex-direction:column;align-items:center;gap:6px;min-width:58px;";
      const n = document.createElement("div");
      n.className = "node";
      n.style.cssText = "width:34px;height:34px;border-radius:7px;font-size:10px;";
      n.style.setProperty("--nc", s.c);
      n.innerHTML = `<span class="glyph" style="color:${s.c}">#${i}</span>`;
      const lab = document.createElement("div");
      lab.style.cssText = "font-family:var(--font-mono);font-size:9.5px;color:var(--muted);white-space:nowrap;";
      lab.textContent = s.t;
      w.appendChild(n); w.appendChild(lab);
      track.appendChild(w);
      if (i < LEDGER.length - 1) {
        const e = document.createElement("div");
        e.className = "edge"; e.style.cssText = "min-width:14px;align-self:flex-start;margin-top:16px;";
        track.appendChild(e);
        w._edge = e;
      }
      return { n, lab, def: s };
    });

    function reconstruct(k) {
      chips.forEach((ch, i) => {
        const built = i <= k;
        ch.n.style.opacity = built ? 1 : 0.25;
        ch.n.style.setProperty("--nc", built ? ch.def.c : LINE);
        ch.n.querySelector(".glyph").style.color = built ? ch.def.c : MUTED;
        if (ch.n._edge) {}
      });
      // edges
      [...track.querySelectorAll(".edge")].forEach((e, i) => {
        e.style.background = (i < k) ? "var(--accent-ln)" : "var(--line)";
      });
      const cur = LEDGER[k];
      const status = k >= LEDGER.length - 1 ? "succeeded"
                   : cur.t.includes("crash") ? "crashed → recovering"
                   : "running";
      out.innerHTML = `step <b>#${k}</b> · ${cur.t.replace("✕ ", "").replace(" ✓", "")} · run <b>${status}</b>`;
    }
    slider.addEventListener("input", () => reconstruct(+slider.value));
    reconstruct(+slider.value);
  }

  /* ===================== boot ===================== */
  function boot() {
    hero(); timelines(); crashDemo(); guarantees(); dx(); dashboard(); crashSlider();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
