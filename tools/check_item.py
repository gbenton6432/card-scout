"""
Measures one eBay listing on demand.

  python tools/check_item.py <item number or eBay link> [--save]

Prints the title, price details and the centering / serial reading for the front photo.
With --save (used by the dashboard's "Check a listing" button), the result is also saved
to checks/<item>.json on the repo's "checks" branch, where the dashboard picks it up.
"""
import base64, json, os, re, sys
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from centering import measure  # noqa: E402
from ebay import Ebay, big_image  # noqa: E402


class _NoStore:
    def count_call(self):
        pass


def item_number(text):
    text = text.strip()
    if re.fullmatch(r"\d{9,15}", text):
        return text
    m = re.search(r"/itm/(?:[^/?]+/)?(\d{9,15})", text)
    if m:
        return m.group(1)
    if "ebay.io" in text or "ebay.us" in text:  # share links redirect to the listing
        r = requests.get(text if text.startswith("http") else "https://" + text, allow_redirects=False, timeout=20)
        return item_number(r.headers.get("Location", ""))
    sys.exit(f"Couldn't find an item number in: {text}")


def check(text):
    legacy = item_number(text)
    eb = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(), budget=5)
    it = eb._get("/buy/browse/v1/item/get_item_by_legacy_id", {"legacy_item_id": legacy})
    if not it:
        return legacy, {"item": legacy, "error": "Listing not found (ended or removed?)"}
    img = big_image((it.get("image") or {}).get("imageUrl"))
    data = requests.get(img, timeout=30).content
    c = measure(data)
    try:
        from serials import read_serial
        c["sn"] = read_serial(data, c.get("quad"))
    except Exception as e:
        c["sn_error"] = type(e).__name__
    seller = it.get("seller") or {}
    return legacy, {
        "item": legacy, "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": it.get("title"), "url": it.get("itemWebUrl"), "img": img,
        "options": it.get("buyingOptions"), "price": (it.get("price") or {}).get("value"),
        "bid": (it.get("currentBidPrice") or {}).get("value"), "bids": it.get("bidCount"),
        "ends": it.get("itemEndDate"), "seller": seller.get("username"),
        "feedback": seller.get("feedbackScore"), "feedback_pct": seller.get("feedbackPercentage"),
        "c": c, "copies": copies(it),
    }


def copies(it):
    """'1', '4', 'more than 10', or None when eBay doesn't say."""
    if it.get("itemGroupHref") or it.get("itemGroupType"):
        return "several (pick-your-card listing)"
    for a in it.get("estimatedAvailabilities") or []:
        if a.get("availabilityThresholdType") == "MORE_THAN":
            return f"more than {a.get('availabilityThreshold')}"
        q = a.get("estimatedAvailableQuantity") or a.get("estimatedRemainingQuantity")
        if q:
            return str(q)
    return None


def key_for(text):
    """Name the dashboard looks for: the item number if the text has one, else the share code."""
    m = re.search(r"(\d{9,15})", text)
    return m.group(1) if m else re.sub(r"[^A-Za-z0-9]", "", text.rstrip("/").split("/")[-1].split("?")[0])


def put_file(path_in_repo, raw_bytes, message):
    repo, tok = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    api = f"https://api.github.com/repos/{repo}"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    path = f"{api}/contents/{path_in_repo}"
    old = requests.get(path, headers=h, params={"ref": "checks"}, timeout=20)
    body = {"message": message, "branch": "checks", "content": base64.b64encode(raw_bytes).decode()}
    if old.ok:
        body["sha"] = old.json()["sha"]
    return requests.put(path, headers=h, json=body, timeout=30).status_code


def save(legacy, result):
    """Write checks/<key>.json to the 'checks' branch (created if needed)."""
    repo, tok = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    api = f"https://api.github.com/repos/{repo}"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    if requests.get(f"{api}/git/ref/heads/checks", headers=h, timeout=20).status_code == 404:
        main_sha = requests.get(f"{api}/git/ref/heads/main", headers=h, timeout=20).json()["object"]["sha"]
        requests.post(f"{api}/git/refs", headers=h, json={"ref": "refs/heads/checks", "sha": main_sha}, timeout=20)
    path = f"{api}/contents/checks/{legacy}.json"
    old = requests.get(path, headers=h, params={"ref": "checks"}, timeout=20)
    body = {"message": f"Check listing {legacy}", "branch": "checks",
            "content": base64.b64encode(json.dumps(result, indent=1).encode()).decode()}
    if old.ok:
        body["sha"] = old.json()["sha"]
    r = requests.put(path, headers=h, json=body, timeout=20)
    print(f"Saved for the dashboard ({r.status_code})")
    if result.get("img"):  # keep the photo too, so a misread can be looked at later
        print("Photo saved", put_file(f"checks/{legacy}.jpg", requests.get(result["img"], timeout=30).content,
                                      f"Photo for listing {legacy}"))


if __name__ == "__main__":
    try:
        legacy, result = check(sys.argv[1])
    except SystemExit as e:
        legacy, result = None, {"error": str(e)}
    legacy = key_for(sys.argv[1])
    result["key"] = legacy
    printable = dict(result, c={k: v for k, v in (result.get("c") or {}).items() if k != "quad"})
    print(json.dumps(printable, indent=2))
    if "--save" in sys.argv:
        save(legacy, result)
