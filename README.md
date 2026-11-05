# Card Scout

Searches eBay as often as eBay's free limit allows (usually hourly) for the cards you list in `config.yaml`, removes consigners and big dealers, measures centering on each front photo, and publishes a dashboard with zoomed-in corners and links to each listing. Everything runs on free GitHub and eBay accounts.

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

You keep two lists, and the scanner pairs them up:

```yaml
gem_rate_min: 40

players:
  - name: Victor Wembanyama
    sport: basketball
    years: 2023-        # 2023 to today
  - name: Pikachu
    sport: pokemon      # no years = every set

sets:                   # normally kept in gem_rates.yaml for you
  - {year: 2023, set: Prizm,        sport: basketball, gem_rate: 46, exclude: [draft picks]}
  - {year: 2024, set: Topps Chrome, sport: basketball, gem_rate: 52}
  - {year: 2023, set: Pokemon 151,  sport: pokemon,    gem_rate: 55}
```

Every player gets searched in every set that matches their sport, falls inside their years, and is at or above `gem_rate_min`. Sets below the cutoff can stay in the list; they're skipped until you lower the cutoff.

Tips:
- Write `set` the way sellers write it in titles ("Prizm", "Topps Chrome", "Obsidian Flames").
- Use `exclude` when a set name also matches another product, e.g. Prizm vs. Prizm Draft Picks.
- Slabs, lots, reprints and customs are excluded from every search (`exclude_words` under `defaults`).
- Searches are grouped: each player's sets from the same year go into one eBay search, and results are matched back to the exact set by title. 11 players across 100+ sets is still only about 100 calls a scan.
- The scanner works out its own pace from eBay's daily limit (5,000 calls on a new account): hourly for most lists, every 2 hours if your list gets very large. Nothing to manage.
- Need more? Request a higher limit through eBay's free **Application Growth Check**, then raise `daily_call_limit` in `config.yaml`.

## Keeping gem rates current

`gem_rates.yaml` holds the PSA 10 gem rate of every set, organized by sport and year, not by player. So:

- **Adding a player** needs no gem rate work. Add one line under `players` and they're searched across every qualifying set already on file.
- **New sets** are added by a monthly scheduled task: Claude reads GemRate in your Chrome (it needs the Claude extension and the computer awake), adds new sets, refreshes the newest sets' rates, and saves the file to your repo.
- **The first fill** is a one-time unattended run of the same thing for 2010 onward. Older sets rarely reach 40%.

## How consigners get removed

A listing is dropped if any of these are true (all adjustable in `config.yaml`):
- The seller is on your `blocked_sellers` list. Each dashboard card shows the seller's username, so add any consigner you spot.
- The title has a word like *consignment* or *vault*.
- The seller's feedback is above `max_seller_feedback` (25,000 by default). Consignment houses and large dealers sit far above that; individual collectors rarely do.
- One seller has more than `max_listings_per_seller` listings across your searches.

The dashboard header shows how many listings were removed each scan. If you think good sellers are being dropped, raise the two numbers.

## Profit estimate

Each card shows **PSA 10 ≈ $X** and an **estimated profit**:

PSA 10 value × (1 − eBay fee) − your price and shipping − grading fee − shipping to PSA

- PSA 10 value comes from current eBay PSA 10 Buy It Now listings of the same card: same player, year, set, card number and parallel. Asking prices run above sale prices, so it uses the lower quartile of the asks, not the average.
- Cards whose PSA 10 version isn't listed right now show "No PSA 10 comps found". Base cards match best; rare parallels often have no comps.
- Fees are in `config.yaml` under `profit` (default: PSA Value tier $39.99, $5 shipping, 13.25% eBay fee).
- Comps refresh once a day, so they barely touch your eBay call budget.

## Alerts

Two kinds, both delivered as GitHub issues (email, plus push through the GitHub app):

- **New Buy It Now listings** that pass the rules under `alerts` in `config.yaml` (default: 55/45 centering or better, at least $50 est. profit, under $500). New auctions don't alert. The first scan only records what's already listed, so you aren't flooded on day one.
- **Auction reminders:** about an hour before any auction on your shortlist ends (45–75 minutes, since the scanner checks twice an hour), with the current bid. Needs shortlist sync (step 6).

## Using the dashboard

- **Sorted by best centering** by default. Green = within PSA 10's 55/45, amber = PSA 9 range (60/40), red = worse.
- **Four corner zooms** per card. Tap one to enlarge and look for white specks, rounding, or fraying.
- **Shortlist** keeps candidates (synced across devices once step 6 is done); **Dismiss** hides ones you've ruled out. **New** marks listings that appeared since your last visit. Dismissed and New are remembered per browser.
- **Centering unreadable** means the photo was too glary, angled, in a slab, or a full-art card with no borders. Those are hidden by default; tap the filter to see them.

## Limits to know

- Centering is from the seller's photo. Crooked or cropped photos can be off by a few points, so treat 55/45–57/43 as borderline and check by eye.
- It reads the first photo as the front. Back centering isn't measured.
- Corners and edges are for you to judge from the zooms; photo quality decides how much you can see.
- eBay allows 5,000 searches a day on a new account. Each search here uses 1–2 calls per scan, so you can track dozens of players.

## Preview without eBay keys

On a computer with Python: `pip install -r requirements.txt`, then `python scout.py --demo`, then open `site/index.html` through a local server (`python -m http.server -d site`) and visit `http://localhost:8000`.
