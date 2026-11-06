"""
Measures one eBay listing on demand: python tools/check_item.py <item number>
Prints the title, price details and the centering / serial reading for its front photo.
Run from GitHub: Actions -> Check a listing -> Run workflow.
"""
import json, os, sys

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from centering import measure  # noqa: E402
from ebay import Ebay, big_image  # noqa: E402


class _NoStore:
    def count_call(self):
        pass


def main(legacy_id):
    eb = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(), budget=5)
    it = eb._get("/buy/browse/v1/item/get_item_by_legacy_id", {"legacy_item_id": legacy_id})
    if not it:
        sys.exit("Listing not found")
    img = big_image((it.get("image") or {}).get("imageUrl"))
    c = measure(requests.get(img, timeout=30).content)
    try:
        from serials import read_serial
        c["serial"] = read_serial(requests.get(img, timeout=30).content, c.get("quad"))
    except Exception as e:
        c["serial"] = f"not read ({type(e).__name__})"
    c.pop("quad", None)
    print(json.dumps({
        "title": it.get("title"), "buying_options": it.get("buyingOptions"),
        "price": (it.get("price") or {}).get("value"), "current_bid": (it.get("currentBidPrice") or {}).get("value"),
        "bids": it.get("bidCount"), "photo": img, "centering": c}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
