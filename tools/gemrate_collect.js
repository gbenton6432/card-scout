// Collects set-level PSA gem rates from GemRate's public Set Summary pages.
// Run in a browser tab already open on www.gemrate.com (it uses that tab's session).
// Used by the monthly refresh. Visits one page per sport per year, ~2 seconds apart.
//
// Result: window.__gr.rows = [[sport, year, set name, total graded, gem rate %], ...]
// for every set with at least MIN_GRADED cards graded.
(async (SPORTS = ["baseball", "basketball", "football", "soccer"], FROM = 2010,
        TO = new Date().getFullYear(), MIN_GRADED = 300) => {
  window.__gr = { done: 0, errors: [], rows: [], counts: {} };
  for (const sport of SPORTS) for (let y = FROM; y <= TO; y++) {
    try {
      const r = await fetch(`/set-details?grader=psa&year=${y}&category=${sport}-cards`, { credentials: "same-origin" });
      const m = (await r.text()).match(/var RowData = (\[.*?\]);\s*\n/s);
      if (!m) window.__gr.errors.push(`${sport} ${y}: HTTP ${r.status}, no data`);
      else {
        const data = JSON.parse(m[1]);
        window.__gr.counts[`${sport} ${y}`] = data.length;
        for (const d of data) if (d.total >= MIN_GRADED)
          window.__gr.rows.push([sport, y, d.name, d.total, Math.round(d.gem_rate * 1000) / 10]);
      }
    } catch (e) { window.__gr.errors.push(`${sport} ${y}: ${e.message}`) }
    window.__gr.done++;
    await new Promise(res => setTimeout(res, 1500 + Math.random() * 1000));
  }
  window.__gr.finished = true;
})();
