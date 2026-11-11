"""
Downloads front photos for a set of eBay searches into one zip on the "checks" branch.
Used to build and test centering references (PSA 10 copies vs raw copies).

  python tools/fetch_photos.py refs/test.zip "name|graded|query" "name|raw|query" ...

graded = PSA 10 listings only (title must say PSA 10), raw = ungraded listings.
"""
import io, json, os, re, sys, zipfile

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from ebay import Ebay, big_image  # noqa: E402
from tools.check_item import put_file, _NoStore  # noqa: E402

GRADED, UNGRADED = 2750, 4000


def fetch(ebay, kind, q, n=12):
    cond = GRADED if kind == "graded" else UNGRADED
    j = ebay._get("/buy/browse/v1/item_summary/search", {
        "q": q, "filter": f"conditionIds:{{{cond}}}", "limit": 100, "category_ids": "261328"}) or {}
    out = []
    for it in j.get("itemSummaries", []):
        t = it.get("title", "")
        if kind == "graded" and not re.search(r"\bpsa\s*10\b", t, re.I):
            continue
        if kind == "raw" and re.search(r"\b(psa|bgs|sgc|cgc|lot)\b", t, re.I):
            continue
        url = big_image((it.get("image") or {}).get("imageUrl"))
        if not url:
            continue
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
        except requests.RequestException:
            continue
        out.append((it["itemId"], t, r.content))
        if len(out) >= n:
            break
    return out


def main():
    dest, specs = sys.argv[1], sys.argv[2:]
    ebay = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(), budget=200)
    buf, manifest = io.BytesIO(), []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for spec in specs:
            name, kind, q = spec.split("|", 2)
            got = fetch(ebay, kind, q)
            print(f"{name}: {len(got)} photos for {q!r}")
            for i, (iid, title, data) in enumerate(got):
                fn = f"{name}/{kind}_{i:02d}.jpg"
                z.writestr(fn, data)
                manifest.append({"file": fn, "kind": kind, "item": iid, "title": title})
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
    print("upload:", put_file(dest, buf.getvalue(), f"Photos for {dest}"))


if __name__ == "__main__":
    main()
