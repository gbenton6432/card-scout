// Collects card-level PSA gem rates for each tracked player from GemRate.
// Run in a browser tab already open on https://www.gemrate.com/player (uses that page's form).
// Input: PLAYERS = [{name, category: "baseball-cards", from: 2018, to: 2026}, ...]
//   When a player's page hits GemRate's 20,000-row cap, the complete set pages for their
//   qualifying sets (set gem rate >= CUTOFF, 3,000+ graded) are read too, filtered to the player.
// Result: window.__gc.rows = [[player, year, set, card number, parallel, graded, gems, psa9], ...]
(async (PLAYERS, CUTOFF = 0.5, MIN_GRADED = 5) => {
  window.__gc = { rows: [], log: [], done: 0, finished: false };
  const pause = () => new Promise(r => setTimeout(r, 1500 + Math.random() * 1000));
  const rowsFrom = h => {
    const i = h.indexOf("var RowData");
    if (i < 0) return [];
    const s = h.indexOf("'", i), e = h.indexOf("');", s + 1), e2 = h.indexOf("';", s + 1);
    const end = [e, e2].filter(x => x > 0).sort((a, b) => a - b)[0];
    return JSON.parse(Function("return '" + h.slice(s + 1, end) + "'")());
  };
  const form = document.querySelector("input[name=player]").form;
  for (const p of PLAYERS) {
    try {
      const fd = new FormData(form);
      if (p.category === "disney-cards") p.category = "non-sport-cards";  // Disney characters live under Non-Sport
      fd.set("grader", "psa"); fd.set("category", p.category); fd.set("player", p.name);
      const h = await (await fetch("/player", { method: "POST", body: new URLSearchParams(fd), credentials: "same-origin" })).text();
      const rows = rowsFrom(h);
      const seen = new Set();
      for (const r of rows) {
        if (r.name !== p.name || r.total < MIN_GRADED) continue;
        const k = [r.year, r.set_name, r.card_number, r.parallel].join("|");
        seen.add(k);
        window.__gc.rows.push([p.name, +r.year, r.set_name, r.card_number, r.parallel, r.total, r.gems, r.psa_9_plus]);
      }
      window.__gc.log.push(`${p.name}: ${rows.length} rows${rows.length >= 20000 ? " (capped, reading set pages)" : ""}`);
      if (rows.length >= 20000) {
        const sets = [];
        for (let y = Math.max(p.from, 2010); y <= p.to; y++) {
          await pause();
          const h2 = await (await fetch(`/set-details?grader=psa&year=${y}&category=${p.category}`, { credentials: "same-origin" })).text();
          const m = h2.match(/var RowData = (\[.*?\]);\s*\n/s);
          for (const st of m ? JSON.parse(m[1]) : []) if (st.gem_rate >= CUTOFF && st.total >= 3000) sets.push([y, st.name]);
        }
        window.__gc.log.push(`${p.name}: reading ${sets.length} set pages`);
        for (const [year, set] of sets) {
          await pause();
          const u = `/item-details-advanced?grader=psa&year=${year}&category=${p.category}&set_name=${encodeURIComponent(set).replace(/%20/g, "+")}`;
          for (const r of rowsFrom(await (await fetch(u, { credentials: "same-origin" })).text())) {
            if (r.name !== p.name || r.card_total_grades < MIN_GRADED) continue;
            const k = [String(year), set, r.card_number, r.parallel].join("|");
            if (seen.has(k)) continue;
            seen.add(k);
            window.__gc.rows.push([p.name, year, set, r.card_number, r.parallel, r.card_total_grades, r.card_gems, r.g9]);
          }
        }
      }
    } catch (e) { window.__gc.log.push(`${p.name}: ${e.message}`) }
    window.__gc.done++;
    await pause();
  }
  window.__gc.finished = true;
})
