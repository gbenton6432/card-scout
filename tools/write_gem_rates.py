"""
Turns collected GemRate rows into gem_rates.yaml.

Input: a JSON file of [sport, year, set name, total graded, gem rate %] rows
(what tools/gemrate_collect.js leaves in window.__gr.rows).
Usage: python tools/write_gem_rates.py rows.json
"""
import json, sys, os
from datetime import date

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def yq(s):
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid YAML


def main(path):
    rows = json.load(open(path))
    seen, out = set(), []
    for sport, year, name, total, rate in rows:
        k = (sport, int(year), name)
        if k in seen:
            continue
        seen.add(k)
        out.append((sport, int(year), name, int(total), float(rate)))
    out.sort(key=lambda r: (r[0], r[1], -r[3]))
    lines = [
        "# Set-level PSA 10 gem rates from GemRate (gemrate.com Set Summary pages).",
        "# Kept up to date automatically by a monthly refresh; no need to edit by hand.",
        "# Every set with 300+ PSA-graded cards is listed, including ones under your",
        "# cutoff: the scanner needs them to tell e.g. Topps Chrome Update apart from",
        "# Topps Chrome. Only sets at or above gem_rate_min (config.yaml) are searched.",
        f"updated: {date.today().isoformat()}",
        "source: gemrate.com",
        "sets:",
    ]
    for sport, year, name, total, rate in out:
        lines.append(f"  - {{sport: {sport}, year: {year}, set: {yq(name)}, gem_rate: {rate:g}, graded: {total}}}")
    open(os.path.join(HERE, "gem_rates.yaml"), "w").write("\n".join(lines) + "\n")
    print(f"Wrote {len(out)} sets")


if __name__ == "__main__":
    main(sys.argv[1])
