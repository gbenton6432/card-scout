"""Print this app's eBay API call limits and how many are left (eBay Analytics API)."""
import os, sys, json
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from ebay import Ebay  # noqa: E402
from tools.check_item import _NoStore  # noqa: E402
e = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"], _NoStore(), budget=5)
j = e._get("/developer/analytics/v1_beta/rate_limit/", {"api_name": "browse"}) or {}
for api in j.get("rateLimits", []):
    for res in api.get("resources", []):
        for r in res.get("rates", []):
            print(api.get("apiName"), res.get("name"), {k: r.get(k) for k in ("count", "limit", "remaining", "reset", "timeWindow")})
