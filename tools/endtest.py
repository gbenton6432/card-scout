"""Does eBay's Browse API still return an auction after it ends, and with the final price?
Finds auctions ending in the next few minutes, waits until they end, then looks them up."""
import json, os, sys, time
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from ebay import Ebay  # noqa: E402
from tools.check_item import _NoStore  # noqa: E402

e = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(), budget=60)
j = e._get("/buy/browse/v1/item_summary/search", {"q": "topps chrome", "category_ids": "261328",
           "filter": "buyingOptions:{AUCTION}", "sort": "endingSoonest", "limit": 50}) or {}
now = datetime.now(timezone.utc)
pick = []
for it in j.get("itemSummaries", []):
    end = datetime.fromisoformat(it["itemEndDate"].replace("Z", "+00:00"))
    if 60 < (end - now).total_seconds() < 480 and int(it.get("bidCount") or 0) > 0:
        pick.append((it["itemId"], end, it.get("currentBidPrice"), it.get("bidCount")))
    if len(pick) >= 4:
        break
for p in pick:
    print("before end:", p[0], p[1].isoformat(), p[2], "bids", p[3])
last = max(p[1] for p in pick)
time.sleep(max(0, (last - datetime.now(timezone.utc)).total_seconds()) + 90)
for iid, end, bid, n in pick:
    try:
        d = e.item(iid)
    except Exception as ex:
        print("after end:", iid, "ERROR", ex); continue
    if d is None:
        print("after end:", iid, "NOT FOUND"); continue
    keep = {k: d.get(k) for k in ("itemEndDate", "currentBidPrice", "price", "bidCount", "estimatedAvailabilities",
                                  "buyingOptions", "itemWebUrl")}
    print("after end:", iid, json.dumps(keep)[:600])
