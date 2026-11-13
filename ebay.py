"""eBay Browse API client that counts every call against the daily budget."""
import base64, re, sys, time

import requests

API = "https://api.ebay.com"
CATEGORIES = {"sports": "261328",  # Sports Trading Card Singles
              "tcg": "183454",     # CCG Individual Cards (Pokémon, Magic, etc.)
              "nonsport": "183050"}  # Non-Sport Trading Card Singles (Disney, etc.)
UNGRADED = "4000"                  # eBay trading-card condition id for "Ungraded"
PAGE = 200                         # most results eBay returns per call
MAX_RESULTS = 10000                # most results eBay will page through for one search


class OutOfBudget(Exception):
    pass


class Ebay:
    def __init__(self, cid, secret, store, budget):
        self.cid, self.secret, self.store, self.budget = cid, secret, store, budget
        self.used, self._tok, self._tok_at = 0, None, 0
        self.http = requests.Session()

    def token(self):
        if self._tok and time.time() - self._tok_at < 6000:
            return self._tok
        r = self.http.post(
            f"{API}/identity/v1/oauth2/token",
            headers={"Authorization": "Basic " + base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode(),
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            timeout=30)
        if r.status_code != 200:
            sys.exit(f"eBay sign-in failed ({r.status_code}): {r.text[:300]}\n"
                     "Check the App ID / Cert ID, and that your Production keyset is enabled.")
        self._tok, self._tok_at = r.json()["access_token"], time.time()
        return self._tok

    def _get(self, path, params=None):
        if self.used >= self.budget:
            raise OutOfBudget()
        for attempt in range(3):
            r = self.http.get(f"{API}{path}", params=params, timeout=40,
                              headers={"Authorization": f"Bearer {self.token()}",
                                       "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"})
            self.used += 1
            self.store.count_call()
            if r.status_code == 429:  # eBay says slow down / daily limit hit
                raise OutOfBudget()
            if r.status_code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                sys.exit(f"eBay refused the request ({r.status_code}): {r.text[:300]}\n"
                         "Your keyset may need Browse API access (README, step 1).")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    def search(self, q, category, d, cons, lo=None, hi=None, offset=0, newest=True):
        """One page of raw-card results. Returns (items, total)."""
        f = [f"buyingOptions:{{{'|'.join(d.get('buying_options', ['FIXED_PRICE', 'AUCTION']))}}}"]
        if d.get("raw_only", True):
            f.append(f"conditionIds:{{{UNGRADED}}}")
        if lo is not None or hi is not None:
            f += [f"price:[{'' if lo is None else lo}..{'' if hi is None else hi}]", "priceCurrency:USD"]
        if d.get("ship_to_country"):
            f.append(f"deliveryCountry:{d['ship_to_country']}")
        blocked = cons.get("blocked_sellers") or []
        if blocked:
            f.append(f"excludeSellers:{{{'|'.join(blocked[:250])}}}")
        params = {"q": q, "filter": ",".join(f), "limit": PAGE, "offset": offset,
                  "category_ids": CATEGORIES.get(category, category)}
        if newest:
            params["sort"] = "newlyListed"
        j = self._get("/buy/browse/v1/item_summary/search", params) or {}
        return j.get("itemSummaries", []), int(j.get("total", 0))

    def item(self, iid):
        return self._get(f"/buy/browse/v1/item/{requests.utils.quote(iid, safe='')}")

    def get_items(self, ids):
        """Up to 20 listings in one call. Listings that have ended or sold are left out."""
        return self._get("/buy/browse/v1/item/", {"item_ids": ",".join(ids[:20])})


def big_image(url):
    return re.sub(r"s-l\d+", "s-l1600", url or "")
