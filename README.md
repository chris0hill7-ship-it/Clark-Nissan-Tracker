# Clark Nissan Abilene — Lot Tracker

A self-updating website that tracks Clark Nissan of Abilene's inventory.
A GitHub Action scrapes the dealer's site on a schedule and commits the
results; the site (`index.html`) reads that data and displays it, flagging
new arrivals, sold vehicles, and price changes automatically.

Once set up, you never have to run anything yourself again.

## One-time setup (about 10-15 minutes)

### 1. Create a GitHub account
Go to https://github.com/signup if you don't already have one. Free.

### 2. Create a new repository
1. Click the **+** in the top-right corner → **New repository**
2. Name it something like `clark-nissan-tracker`
3. Set it to **Public** (GitHub Pages requires this on free accounts)
4. Do NOT check "Add a README" (we already have one)
5. Click **Create repository**

### 3. Upload these files
On the new repo's page:
1. Click **Add file → Upload files**
2. Drag in everything from this folder, **keeping the folder structure**:
   - `index.html`
   - `scraper.py`
   - `README.md`
   - `.github/workflows/scrape.yml`
   - `data/snapshots/.gitkeep`

   (If the drag-and-drop flattens folders, instead use "uploading via the
   command line" — see step 3b below.)
3. Scroll down, click **Commit changes**

**3b. Alternative (recommended, avoids folder issues) — using Git directly:**
If you have Git installed, this is more reliable than the web upload:
```
git clone https://github.com/YOUR-USERNAME/clark-nissan-tracker.git
cd clark-nissan-tracker
# copy all the files from this project into this folder, preserving structure
git add .
git commit -m "Initial setup"
git push
```

### 4. Turn on GitHub Pages
1. In your repo, go to **Settings → Pages**
2. Under "Build and deployment" → Source, choose **Deploy from a branch**
3. Branch: `main`, folder: `/ (root)` → **Save**
4. After a minute or two, your site will be live at:
   `https://YOUR-USERNAME.github.io/clark-nissan-tracker/`

### 5. Allow the workflow to commit data
1. Go to **Settings → Actions → General**
2. Scroll to "Workflow permissions"
3. Select **Read and write permissions** → **Save**

### 6. Run the scraper for the first time
1. Go to the **Actions** tab in your repo
2. Click **Scrape Clark Nissan Inventory** in the left sidebar
3. Click **Run workflow** (dropdown button, top right) → **Run workflow**
4. Wait ~1-2 minutes, refresh — it should show a green checkmark
5. Visit your site URL from step 4 — inventory should now be populated

That's it. From now on, it re-scrapes automatically every day (currently set
to 11:00 UTC / roughly 5-6am Texas time) and the site updates itself with
whatever changed.

## Changing the schedule
Edit `.github/workflows/scrape.yml`, the line:
```yaml
- cron: '0 11 * * *'
```
Cron format is `minute hour day month weekday`, all in UTC. For example,
`0 */6 * * *` runs every 6 hours instead of once a day.

## If the scraper breaks
Dealer websites occasionally change their page layout, which can break the
parsing logic in `scraper.py`. If the Actions tab shows a red X, click into
the failed run to see the error, or just paste it back for help fixing the
selectors in `parse_vehicle_block()`.

## A note on scraping
This pulls public inventory data from abilenenissan.com on a schedule.
That's generally fine for personal tracking, but repeated automated access
to a commercial site can brush up against its Terms of Service — worth
keeping in mind if this ever becomes more than a personal project.
