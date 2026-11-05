# Card Scout

Keeps track of every raw eBay listing for your players in sets with a high PSA 10 gem rate, removes consigners and big dealers, measures centering on each front photo, and publishes a dashboard with zoomed-in corners, price filters and links to each listing and its PSA 10 sold comps. Everything runs on free GitHub and eBay accounts.

Setup takes about 30–40 minutes and is much easier on a computer than a phone.

---

## 1. Get your free eBay API keys

1. Go to **developer.ebay.com** and click **Register**. This is a separate developer account; any email works.
2. Confirm your email and wait for approval (often minutes, up to one business day).
3. Sign in, then open **Application Keys** from your account menu.
4. Enter an application name (`card-scout`) and click **Create a keyset** under **Production**.
5. You'll see **"Your Keyset is currently disabled."** Click the link in that message.
6. On the **Marketplace Account Deletion** page, slide **Not persisting eBay data** to On, then **Confirm**.
7. Choose the **Exemption reason** that fits, and in **Additional information** write something accurate like: *Personal, non-commercial tool. Searches active listings hourly; each run replaces the previous results, and nothing is kept once a listing ends.* Click **Submit** and wait for **Exemption Enabled**.
8. Back on **Application Keys**, copy the **App ID (Client ID)** and **Cert ID (Client Secret)** from the Production column. Keep them private.

> If a scan later fails with an "access refused" message, request Browse API access through eBay's free **Application Growth Check**.

## 2. Put the code on GitHub (free)

1. Create an account at **github.com**.
2. Click **+ → New repository**. Name it `card-scout`, set it to **Public** (free dashboard hosting requires public), and create it.
3. Click **uploading an existing file**, then drag in everything *inside* the `card-scout` folder (not the folder itself), **including the hidden `.github` folder**. On a Mac, press `Cmd + Shift + .` in Finder to show hidden folders. Click **Commit changes**.
4. A red ❌ run may appear in the **Actions** tab right away. That's expected until your keys are added in step 3.

## 3. Add your keys to GitHub

In your repo: **Settings → Secrets and variables → Actions → New repository secret**. Add two:

| Name | Value |
|---|---|
| `EBAY_CLIENT_ID` | your App ID |
| `EBAY_CLIENT_SECRET` | your Cert ID |

GitHub hides these; they never appear on the dashboard.

## 4. Turn on the dashboard

1. **Settings → Pages → Source:** choose **GitHub Actions**.
2. **Actions** tab → **Scan eBay** → **Run workflow**.
3. After 2–3 minutes it turns green. Your dashboard is at
   `https://YOUR-GITHUB-USERNAME.github.io/card-scout/`
   Bookmark it or add it to your phone's home screen.

It then rescans on its own, as often as your eBay call limit allows, and immediately whenever you edit `config.yaml`.

## 5. Get alerts on your phone

1. Install the **GitHub** app and sign in.
2. Allow notifications when it asks (or later in your phone's Settings → GitHub).
3. In the app, check **Profile → Settings → Notifications** has push turned on for **Issues**.

Email alerts work without this; GitHub sends them to your account email automatically.

## 6. Turn on shortlist sync (needed for auction reminders)

Your shortlist has to live in your repo for the scanner to see it. This also makes it the same on your phone and computer.

1. On GitHub: **profile picture → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Name it `card-scout shortlist`. Expiration: the longest offered (you'll redo this step when it expires).
3. **Repository access:** Only select repositories → `card-scout`.
4. **Permissions → Repository permissions → Contents:** Read and write. Leave everything else as is.
5. **Generate token** and copy it.
6. Open your dashboard, tap **Shortlist sync: off**, paste the token, tap **Save and sync**.
7. Do step 6 once on each device you use (the token stays in that browser only).

## 7. Connect the repo to Claude (for automatic gem rates)

In the Claude app, connect GitHub and give it access to the `card-scout` repo. Claude then keeps `gem_rates.yaml` up to date for you, and the dashboard rescans whenever it changes. See **Keeping gem rates current** below.

---

## Choosing what it searches

Edit `config.yaml` on GitHub (open the file, tap the pencil, commit). A new scan starts right away.

```yaml
gem_rate_min: 50

players:
  - {name: Victor Wembanyama, sport: basketball, years: 2023-}   # 2023 to today
  - {name: LeBron James,      sport: basketball, years: 2003-}
```

- A card is kept when its **set's** overall PSA 10 gem rate (from `gem_rates.yaml`) is at or above `gem_rate_min`, whatever player is on it.
- `sport` must be baseball, basketball, football or soccer (the sports in `gem_rates.yaml`).
- Slabs, lots, reprints and customs are excluded from every search (`exclude_words` under `defaults`).
- Price limits for what's collected are `min_price` / `max_price` under `defaults`. The dashboard has its own price filter on top.

## How scanning works

eBay limits an app to about 5,000 requests a day, 200 listings per request, and 10,000 results for any one search. To still see every listing, the scanner keeps a database of what it has found (stored between runs) and spends the day's requests in two ways each hour:

1. **New listings:** one newest-first search per player ("Shohei Ohtani"), stopping as soon as it reaches listings it has already seen. About 50 requests an hour for 50 players.
2. **Inventory sweep:** everything left over in the hour's share of the budget goes to working through each player's full catalog, one player-year at a time ("2018 Shohei Ohtani"). A player-year with more than 10,000 listings is automatically split into price bands. Finishing a player-year also tells it which listings have sold or ended.

Each listing's title is matched to its exact year and set (the longest matching set name wins, so Topps Chrome Update isn't mistaken for Topps Chrome), and only qualifying sets are kept. Each photo is measured once, in the background, up to 25 minutes per run, so a big first sweep takes a few runs to finish measuring.

The dashboard header shows how far the first sweep has got and how often a full sweep cycles. If a full cycle takes too long for your list, request a higher limit through eBay's free **Application Growth Check** and raise `daily_call_limit` in `config.yaml`.

## Keeping gem rates current

`gem_rates.yaml` holds the PSA 10 gem rate of every baseball, basketball, football and soccer set from 2010 on with 300+ PSA-graded cards, read from GemRate's Set Summary pages. It's organized by sport and year, not by player, so:

- **Adding a player** needs no gem rate work. Add one line under `players` and they're searched across every qualifying set already on file.
- **New sets and updated rates** come from a monthly scheduled task. Claude opens GemRate in the Claude desktop app's built-in browser (the app needs to be open and the computer awake), re-reads the Set Summary pages, and saves the new file here. The dashboard rescans when it changes.
- The scripts in `tools/` are what the refresh runs.

## How consigners get removed

A listing is dropped if any of these are true (all adjustable in `config.yaml`):
- The seller is on your `blocked_sellers` list. Each dashboard card shows the seller's username, so add any consigner you spot.
- The title has a word like *consignment* or *vault*.
- The seller's feedback is above `max_seller_feedback` (25,000 by default). Consignment houses and large dealers sit far above that; individual collectors rarely do.
- One seller has more than `max_listings_per_seller` listings across your searches.

The dashboard header shows how many listings were removed each scan. If you think good sellers are being dropped, raise the two numbers.

## Prices

- **PSA 10 sold comps:** each card has a button that opens eBay's sold listings for the PSA 10 version of that card. There's no free automated source of sold prices, so this is a one-tap manual check.
- **Break-even:** the sale price that covers what you paid plus all-in grading, after eBay's 13.25% fee, for PSA Value ($45) and Value Bulk ($30). Change the amounts under `grading` in `config.yaml`.
- **Price filter and sorts:** set a min and max asking price; sort by price either way or by **Newly listed**.
- **Accepts offers:** shown on the card and available as a filter.

## Serial numbers

The scanner reads the serial stamp (e.g. 023/199) **off the card photo**, not the title, using free OCR that runs on GitHub's servers. Filter by number and print run (e.g. 23 / 99), or pick **Jersey match**, **1st (1/x)**, **Last (x/x)** or **1/1**. Jersey numbers are set per player in `config.yaml`. Foil glare, dark photos and tiny stamps can cause misreads, so always check the stamp in the listing before buying.

## Alerts

Two kinds, both delivered as GitHub issues (email, plus push through the GitHub app):

- **New Buy It Now listings** that pass the rules under `alerts` in `config.yaml` (default: 55/45 centering or better, under $500, and no more than 2× the typical ask for that card). New auctions don't alert. The first scan only records what's already listed, so you aren't flooded on day one.
- **Auction reminders:** about an hour before any auction on your shortlist ends (45–75 minutes, since the scanner checks twice an hour), with the current bid. Needs shortlist sync (step 6).

## Using the dashboard

- **Sorted by best centering** by default. Green = within PSA 10's 55/45, amber = PSA 9 range (60/40), red = worse.
- **Four corner zooms** per card. Tap one to enlarge and look for white specks, rounding, or fraying.
- **Shortlist** keeps candidates (synced across devices once step 6 is done); **Dismiss** hides ones you've ruled out. **New** marks listings that appeared since your last visit. Dismissed and New are remembered per browser.
- **Removed automatically:** cards whose photo is clear and measures worse than 55/45 (with 1.5 points allowed for measurement error).
- **Centering unclear** means the photo was too glary, angled, in a slab, or borderless. Those stay visible for you to judge; **Measured only** hides them.
- **Measuring soon** means the photo is still queued.
- Results show 40 at a time; **Show more** loads the next 40. With a very large inventory the dashboard opens on one player.

## Limits to know

- Centering is from the seller's photo. Crooked or cropped photos can be off by a few points, so treat 55/45–57/43 as borderline and check by eye.
- It reads the first photo as the front. Back centering isn't measured.
- Corners and edges are for you to judge from the zooms; photo quality decides how much you can see.
- eBay allows about 5,000 requests a day on a new account. New listings are always caught within the hour; how often older listings are re-checked depends on how big your players' inventory is (see How scanning works).

## Preview without eBay keys

On a computer with Python: `pip install -r requirements.txt`, then `python scout.py --demo`, or `python scout.py --demo-scale` for tens of thousands of made-up listings, then open `site/index.html` through a local server (`python -m http.server -d site`) and visit `http://localhost:8000`.
