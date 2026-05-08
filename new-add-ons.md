# New Add-Ons Plan
**Client:** Joy Gebarah  
**Agreed Price:** $75 (filters) + $75 (quick lookup + saved CSV) = **$150 total**

---

## Add-On 1 — Comparable Filter Settings ($75)

### What Joy Wants
Before generating PDFs, Joy wants configurable input boxes to set tolerance thresholds. The app will only pick sold properties that match the client's property within those tolerances — not just nearest by distance.

### Filters to Build
| Filter | Input Type | Example Value | Data Available? |
|---|---|---|---|
| Sq Ft within % | Number input | 15 | Yes — both CSVs have `Sq Ft` |
| Year Built within years | Number input | 20 | Yes — both CSVs have `Yr Built` |
| Beds within N | Number input | 1 | Yes — both CSVs have `Beds` |
| Lot Size within % | Number input | 30 | No — Joy needs to add column to both CSVs |

> **Note:** Lot size filter is on hold until Joy's exports include a `Lot Size` column. The first 3 filters work with his current spreadsheets as-is.

### Fallback Logic (Important)
If the filters are too strict and fewer than the requested number of comparables are found, the app falls back to nearest-by-distance so mailers are never empty.

Example: Joy wants 3 comparables, filters only find 1 match — app uses that 1 filtered match + 2 nearest by distance as backup.

### Files to Change

**`mailer_generator.py` — `find_nearest_sold()` (line 457)**
- Change signature to accept `client_row` (full client data) and `filter_settings` dict
- Apply filters before distance sort:
  - `abs(client_sqft - comp_sqft) / client_sqft <= sqft_pct / 100`
  - `abs(client_yr_built - comp_yr_built) <= age_years`
  - `abs(client_beds - comp_beds) <= beds_diff`
- Sort filtered pool by distance, take top N
- If not enough filtered results, fall back to distance-only for remaining slots

**`mailer_generator.py` — `generate_mailers()` (line 468)**
- Add `filter_settings` parameter (dict with sqft_pct, age_years, beds_diff)
- Read `Sq Ft`, `Yr Built`, `Beds` from client CSV (columns already exist, just not used)
- Pass client row data into `find_nearest_sold()` call

**`app.py` — `/generate` route (line 222)**
- Read 3 new form fields: `sqft_pct`, `age_years`, `beds_diff`
- Pass them into `params` dict → `generate_mailers()`
- Default values if left blank: sqft=15%, age=20 years, beds=1

**`templates/index.html`**
- Add a "Comparable Filters" section to the form
- 3 number inputs with defaults pre-filled
- Small hint text under each explaining what it does

### Estimated Work: 4-5 hours

---

## Add-On 2 — Quick Single Address Lookup + Saved Sold CSV ($75)

Two related features Joy confirmed he wants.

### Feature 2A — Type-In Single Address (no CSV needed)
Joy wants to type one address directly and get back a quick list of nearby comparables — without making a spreadsheet just for one address.

**What it shows:**
- A table of nearby sold properties matching his filter settings
- No PDF generated — just a fast on-screen result
- Uses his saved sold CSV (Feature 2B)

**Files to Change:**

**`app.py`** — New route `/lookup`
- Accepts: single address (text) + filter settings
- Geocodes the address via Mapbox
- Loads saved sold CSV from `saved_data/master_sold.csv`
- Runs `find_nearest_sold()` with filters applied
- Returns JSON with comparable results

**`templates/index.html`**
- Add a "Quick Lookup" section above the batch form
- Address text input
- "Find Comparables" button
- Results table appears below on submit (AJAX, no page reload)

---

### Feature 2B — Save Master Sold CSV on Server
Joy wants to upload his sold CSV once, have it persist, and just replace it monthly when data changes.

**How it works:**
- Dedicated "Upload Master Sold List" section on the page
- Saves to `saved_data/master_sold.csv` on the server
- Shows last updated date so Joy knows how fresh it is
- On the main batch form: toggle to "Use saved sold list" instead of uploading a file each time
- Joy swaps it out monthly by uploading a new file

**Files to Change:**

**`app.py`** — New route `/upload-sold`
- Accepts sold CSV upload
- Saves to `saved_data/master_sold.csv`
- Saves a timestamp alongside it
- Returns success + last updated date

**`app.py`** — Update `/generate` and `/lookup` routes
- If "use saved list" is selected, load from `saved_data/master_sold.csv`
- Validate file exists, else show error: "No saved list found — please upload one first"

**`templates/index.html`**
- "Saved Sold List" section:
  - Shows last updated date if a file exists
  - Upload button to replace it
  - "Use saved list" toggle on the main batch form

> **Deployment Note:** The `saved_data/` folder must persist between server restarts and redeploys. Need to verify this with whatever hosting platform we use (Railway, Render, etc.) before building — some platforms wipe the filesystem on redeploy.

### Estimated Work: 4-5 hours

---

## Full Summary

| Feature | Price | Est. Hours | Status |
|---|---|---|---|
| Filter settings (sqft %, age, beds) | $75 | 4-5 hrs | Ready — no CSV changes needed |
| Quick address lookup (type-in) | included in $75 | — | Ready to build |
| Save master sold CSV on server | included in $75 | — | Ready — check deployment first |
| Lot size filter | future | 1-2 hrs | Needs Joy to update his CSV exports |
| Property type filter | skip for now | — | All Joy's data is same type anyway |

**Total agreed: $150**

---

## CSV Changes Required from Joy

| Feature | Client CSV | Sold CSV |
|---|---|---|
| All current add-ons | No changes needed | No changes needed |
| Lot size filter (future) | Add `Lot Size` column | Add `Lot Size` column |

Joy's current CSVs already have everything needed for the 3 active filters (Sq Ft, Yr Built, Beds).
