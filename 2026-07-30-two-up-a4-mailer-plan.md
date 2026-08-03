# 2-Up A4 Mailer Layout — Implementation Plan

**Client:** Joy Gebarah
**Agreed Price:** $85 (confirmed Jul 30, 2026)
**Date written:** 2026-07-30
**Status:** Implemented and verified 2026-07-30 — all 9 build steps done, 46/46 integration
checks passing (17 test groups, including 3 new 2-up tests), real Mapbox/WeasyPrint end-to-end
run confirmed pages are exactly 8.27in x 11.69in. Not yet committed to git — awaiting explicit
go-ahead. Still open: exact right-side panel width pending USPS confirmation (§2, one-line change
in the constants block when it arrives).
**Approved mockup:** https://claude.ai/code/artifact/f6f44bf6-7ae7-4be6-af4b-5367b226c476

> **Read this first if you're new to the repo.** Every file path and line number below was
> checked against the actual code on 2026-07-30. Nothing here is guessed. Where something is
> genuinely unknown (USPS sizing), it's called out explicitly as a blocker instead of being
> filled in with a made-up number.

---

## 1. What we're building, in one paragraph

Today the app prints **one tri-fold mailer per 8.5" x 11" page** — three stacked panels, one
client per page. Joy wants a **second layout option**: an **A4 sheet (8.27" x 11.69") holding
two completely separate mailers stacked on top of each other**. He cuts the sheet in half and
gets two mailers, each 8.27" wide x 5.83" tall. On each half: the **left two-thirds** carries the
Recent Sales table and the neighborhood map, and the **right one-third** is a real mailing panel
with the recipient's name, address, city, ZIP, and a USPS permit indicia in the top-right corner.

---

## 2. What Joy has explicitly signed off on

| # | Requirement | Confirmed when | Notes |
|---|---|---|---|
| 1 | A4 sheet 8.27" x 11.69", two mailers stacked | Jul 19 | Each half = 5.83" x 8.27" |
| 2 | Left 2/3 = Recent Sales + map | Jul 19 | |
| 3 | Right 1/3 = Full Name / Address / City / ZIP | Jul 19 | |
| 4 | USPS permit indicia at exactly 1" x 1" | Jul 28 | File provided (see section 4.3) |
| 5 | No return address block | Jul 28 | "For now I will not put any info for them" |
| 6 | Support 5–7 sales rows, not just 3 | Jul 21 | Map shrinks to fit |
| 7 | Box colour `#2E72F9`, box text `#D2F249` | Jul 30 | Replaces the brown/gold theme |
| 8 | Map 33% smaller (print safety margin) | Jul 30 | So the cut doesn't clip the map |
| 9 | Property table text bold black | Jul 30 | Was washing out |
| 10 | Map caption reads "Recent Public Records Sales" | Jul 30 | No "Mapbox" wording on the mailer |

### Still blocked — do not hardcode these

| Item | Why it's blocked | What to do |
|---|---|---|
| **Exact right-side panel width** | Joy is waiting on his Post Office to confirm the USPS-required address-block zone | Build with the values in section 4.1 but put them in a single constants block (Step 2) so it's a **one-line change** when the number arrives |

---

## 3. How the system works today (so you know what you're touching)

Read these three files before changing anything.

### `mailer_generator.py` — all PDF logic lives here

| Lines | What it does |
|---|---|
| 28–380 | `HTML_TEMPLATE` — one giant Jinja2 HTML string, the tri-fold design |
| 385–403 | `image_to_base64()` — turns an image file into a `data:` URI so WeasyPrint can embed it |
| 406–422 | `load_cache()` / `save_cache()` — persistent geocoding cache, thread-safe |
| 425–457 | `geocode_address()` — Mapbox geocoding, cached |
| 460–531 | `find_nearest_sold()` — picks N comparable sales, with filter + distance fallback |
| 536–708 | `generate_mailers()` — the main loop |
| 634 | `for idx, (index, client) in enumerate(valid_clients.iterrows())` — **one iteration = one client = one PDF** |
| 639–649 | Builds the Mapbox static map URL (`500x400@2x`) |
| 667–678 | `template.render(...)` |
| 680–682 | Writes one PDF per client, appends to `pdf_files` |
| 687–698 | Merges all PDFs into `final_mailers_trifold.pdf` |

### `app.py` — Flask layer

| Lines | What it does |
|---|---|
| 111–170 | `run_generation()` — background thread worker, builds the ZIP |
| 127–139 | The call into `generate_mailers()` |
| 186–308 | `/generate` route — validates uploads, parses the form, starts the thread |
| 252–264 | Where form fields get read (`num_nearby`, `num_clients`, filters) |
| 288–298 | The `params` dict handed to the worker |

### `templates/index.html` — the form

| Lines | What it does |
|---|---|
| 17–18 | Page title + subtitle ("Tri-Fold Mailer Generator", `8.5" x 11"`) |
| 188–194 | `num_nearby` dropdown — **currently caps at 5**, Joy needs 7 |
| 389 | Result text: `Generated ${data.pdf_count} tri-fold mailer PDFs (8.5" x 11").` |

### The key structural fact

> **Today: 1 client -> 1 HTML render -> 1 PDF -> merged.**
> **New: 2 clients -> 1 HTML render (one A4 sheet) -> 1 PDF -> merged.**
>
> That is the whole change in one sentence. Everything else is styling and plumbing.

---

## 4. The exact geometry (measured, not eyeballed)

### 4.1 Page and panel dimensions

```
A4 sheet                8.27in wide  x  11.69in tall
  |- Mailer #1 (top)    8.27in wide  x   5.845in tall   (11.69 / 2)
  |- Mailer #2 (bottom) 8.27in wide  x   5.845in tall
```

Inside **each** mailer:

| Region | Width calc | Width | Inner width (after 0.2in padding each side) |
|---|---|---|---|
| Left content side | 8.27 x 66.66% | **5.513in** | 5.113in |
| Right address side | 8.27 x 33.33% | **2.757in** | 2.357in |

Vertical space inside the content side: `5.845 - 0.4 (padding) = 5.445in`

Approximate vertical budget (verify by measuring the real PDF — see section 6):

| Element | 3-row table | 7-row table |
|---|---|---|
| Header bar (`.mini-header`) | ~0.41in | ~0.41in |
| Section title | ~0.24in | ~0.24in |
| Sales table | ~0.72in | ~1.06in |
| **Map box (fixed)** | **2.60in** | **2.60in** |
| Map legend | ~0.12in | ~0.12in |
| Spacer (absorbs the rest) | ~1.36in | ~1.02in |

### 4.2 Why the map gets a FIXED height, not a flex ratio

This is the one genuinely subtle thing in this job. Read it carefully.

The map image is fetched from Mapbox at a fixed pixel size and displayed with
`object-fit: cover` (`mailer_generator.py:191`). `cover` means: **scale to fill the box, then
crop whatever overflows.**

- Current Mapbox request: `500x400@2x` -> delivers 1000x800px -> **aspect ratio 1.25**
- New map box: 5.113in wide x 2.60in tall -> **aspect ratio 1.97**

If we leave the request at `500x400`, a 1.25-ratio image forced into a 1.97-ratio box gets
scaled to match the width, then roughly **30% of its height is cropped off — top and bottom.**
The red "your home" pin and the green sale pins live near the edges. **They would get silently
cut off and nobody would notice until Joy printed 500 mailers.**

**The fix:** make the box height a fixed constant, then request the map at that exact ratio.

```
map box:        5.113in x 2.60in       ratio = 1.97
mapbox request: 500 x 254 @2x          ratio = 1.97   -> 1000x508px delivered
```

Fixed height also means the aspect ratio **does not drift** when the table grows from 3 rows to
7 rows. With a flex ratio the box would be 1.93 at 3 rows and 2.11 at 7 rows, and we would have
to recompute the Mapbox size per client. Fixed height = one number, always correct. This is the
simpler and more traditional choice, and it is the one to build.

> **Sanity note:** 1000x508 is smaller in both dimensions than the 1000x800 the app already
> requests successfully today, so it is comfortably inside Mapbox's static-image size limit.

### 4.3 The USPS permit indicia

- **Source file:** `USPS_Marketing_Mail_Indicia_1x1_300dpi-3.png` (repo root, 7,456 bytes)
- **Content:** `PRSRT STD / U.S. POSTAGE PAID / SAN LUIS OBISPO, CA / PERMIT NO. 146`
- **Currently NOT tracked in git** (verified with `git ls-files`) — it must be committed
- **Move it to:** `static/usps_indicia.png`
- **Render at:** exactly 1in x 1in, positioned `top: 0.2in; right: 0.2in` inside the address side
- `.gitignore` blocks `*.pdf` and `*.zip` but **not** `*.png`, so committing it works as-is
- The `Dockerfile` uses `COPY . .` (line 24), so `static/` ships to production automatically

---

## 5. Implementation steps

Work through these in order. Each step is small enough to test on its own.

---

### Step 1 — Commit the indicia asset

```bash
git mv USPS_Marketing_Mail_Indicia_1x1_300dpi-3.png static/usps_indicia.png
```

If `git mv` fails because the file was never tracked, just move it and
`git add static/usps_indicia.png`.

**Done when:** `git ls-files static/` lists `static/usps_indicia.png`.

---

### Step 2 — Add a constants block to `mailer_generator.py`

Put this near the top of the file, just under the existing `CACHE_FILE` definition (~line 23).
**Every tunable number lives here and nowhere else.** When Joy's USPS measurement arrives, you
change one line in this block and you are done.

```python
# --- 2-UP A4 LAYOUT CONSTANTS ---------------------------------------------
# All physical dimensions for the 2-up layout live here so USPS sizing
# changes are a one-line edit. Joy is still confirming ADDRESS_SIDE_PCT
# with his Post Office as of 2026-07-30.

SHEET_W_IN         = 8.27    # A4 width
SHEET_H_IN         = 11.69   # A4 height
MAILER_H_IN        = 5.845   # SHEET_H_IN / 2

ADDRESS_SIDE_PCT   = 33.33   # right panel width, % of sheet  <-- USPS may change this
CONTENT_SIDE_PCT   = 66.66   # left panel width, % of sheet   <-- and this (must sum to ~100)

PANEL_PAD_IN       = 0.2     # inner padding on both panels
MAP_H_IN           = 2.60    # fixed map height (Joy: "33% smaller")
INDICIA_SIZE_IN    = 1.0     # Joy confirmed exactly 1in x 1in
INDICIA_OFFSET_IN  = 0.2     # distance from top and right edge

BRAND_BOX_BG       = '#2E72F9'   # Joy-specified box colour
BRAND_BOX_TEXT     = '#D2F249'   # Joy-specified text-inside-box colour

# Mapbox static image, sized to the map box ratio so object-fit:cover
# never crops the pins. See plan section 4.2.
_MAP_W_IN          = SHEET_W_IN * (CONTENT_SIDE_PCT / 100) - (PANEL_PAD_IN * 2)
MAP_REQ_W_PX       = 500
MAP_REQ_H_PX       = round(MAP_REQ_W_PX * (MAP_H_IN / _MAP_W_IN))

# Table switches to the compact style at this many rows or more
COMPACT_TABLE_THRESHOLD = 5
```

**Done when:** `python -c "import mailer_generator as m; print(m.MAP_REQ_W_PX, m.MAP_REQ_H_PX)"`
prints `500 254`.

---

### Step 3 — Add proper logging to `mailer_generator.py`

`CLAUDE.md` requires `logger.info/warning/error` with **contextual dicts, never bare strings**.
Right now the module has exactly one `print()` (line 455) and no logger at all.

Add near the imports:

```python
import logging

logger = logging.getLogger(__name__)
```

Replace line 455:

```python
# BEFORE
print(f"Geocoding failed for {address}: {e}")

# AFTER
logger.warning("Geocoding failed", extra={'address': address, 'error': str(e)})
```

Add these log points inside `generate_mailers()`:

```python
logger.info("Mailer generation started", extra={
    'layout': layout, 'clients': total_clients, 'sold': total_sold,
    'num_nearby': num_nearby,
})

logger.info("Sheet rendered", extra={
    'layout': layout, 'sheet': sheet_num, 'clients_on_sheet': len(pair),
})

logger.info("Mailer generation finished", extra={
    'layout': layout, 'sheets': len(pdf_files), 'skipped': skipped_clients,
})

logger.error("Mailer generation failed", extra={'layout': layout, 'error': str(e)})
```

**Done when:** a generation run emits structured log lines and no bare `print()` remains in the file.

---

### Step 4 — Write the new `TWO_UP_TEMPLATE`

Add a **second** template constant in `mailer_generator.py`. **Do not edit `HTML_TEMPLATE`** —
the tri-fold layout must keep working exactly as it does today.

The approved mockup at `mockup_two_up_a4.html` is the source of truth for the markup and CSS.
Copy from it, then make these changes to turn it into a real template:

| Change | Why |
|---|---|
| Delete the `.brief`, `.spec-row`, `.spec-chip`, `.canvas-wrap`, `.canvas-label` CSS and markup | Screen-only explainer chrome for Joy, not part of the printed mailer |
| Delete the `@media (prefers-color-scheme: dark)` and `:root[data-theme=...]` blocks | Print output has no dark mode; these do nothing in a PDF |
| Delete `.cut-label` and the `CUT LINE` div | Guide text must not print on real mail |
| Change `.mailer + .mailer` border from `2px dashed #bbb` to `none` | The dashed cut line is a mockup aid, not something to print |
| Replace the inline `<svg>` fake map with `<img src="{{ mailer.map_url }}">` | Real Mapbox map |
| Replace hardcoded names/addresses with `{{ mailer.first_name }}` etc. | Real data |
| Wrap the second mailer in `{% if mailers|length > 1 %}` | Odd client counts leave the bottom half blank |
| Swap the hardcoded base64 indicia for `{{ indicia_img }}` | Loaded once via `image_to_base64()` |
| Apply `.property-table--compact` conditionally | Only when `num_nearby >= COMPACT_TABLE_THRESHOLD` |

The structure to render:

```html
{% for mailer in mailers %}
<div class="mailer">
  <div class="content-side">
    <div class="mini-header">GEBARAH REAL ESTATE GROUP</div>
    <div class="section-title">Recent Nearby Sales</div>
    <table class="property-table{% if compact %} property-table--compact{% endif %}">
      <tr><th>Address</th><th>Price</th><th>Bed/Bath</th><th>Sq Ft</th></tr>
      {% for p in mailer.nearby %}
      <tr>
        <td>{{ p['Address'][:25] }}{% if p['Address']|length > 25 %}...{% endif %}</td>
        <td class="price-cell">${{ "{:,.0f}".format(p['Purchase Amt'] / 1000) }}k</td>
        <td>{{ p['Beds'] }}/{{ p['Baths'] }}</td>
        <td>{{ "{:,.0f}".format(p['Sq Ft']) }}</td>
      </tr>
      {% endfor %}
    </table>
    <div class="map-box"><img src="{{ mailer.map_url }}" alt="Recent Public Records Sales"></div>
    <div class="map-legend">Recent Public Records Sales</div>
    <div class="map-spacer"></div>
  </div>
  <div class="address-side">
    {% if indicia_img %}
    <div class="stamp-box"><img src="{{ indicia_img }}" alt="USPS Permit Indicia"></div>
    {% endif %}
    <div class="address-block">
      <div class="address-line address-name">{{ mailer.first_name }} {{ mailer.last_name }}</div>
      <div class="address-line">{{ mailer.address }}</div>
      <div class="address-line">{{ mailer.city }}, CA {{ mailer.zip_code }}</div>
    </div>
  </div>
</div>
{% endfor %}
```

Critical CSS values (these carry Joy's Jul 30 feedback):

```css
@page { size: 8.27in 11.69in; margin: 0; }

.mailer      { width: 8.27in; height: 5.845in; display: flex; overflow: hidden; }
.content-side{ width: 66.66%; padding: 0.2in; display: flex; flex-direction: column; }
.address-side{ width: 33.33%; padding: 0.2in; position: relative; display: flex; flex-direction: column; }

/* Joy #7 - box colours */
.mini-header       { background: #2E72F9; color: #D2F249; }
.property-table th { background: #2E72F9; color: #D2F249; }

/* Joy #9 - bold black property text */
.property-table td            { color: #000; font-weight: bold; }
.property-table td.price-cell { color: #27ae60; font-weight: bold; }

/* Joy #8 - fixed smaller map + spacer that absorbs the leftover */
.map-box     { height: 2.60in; overflow: hidden; border: 2px solid #D4AF37; border-radius: 6px; }
.map-box img { width: 100%; height: 100%; object-fit: cover; }
.map-spacer  { flex: 1; }

/* Joy #4 - exactly 1in x 1in indicia */
.stamp-box     { position: absolute; top: 0.2in; right: 0.2in; width: 1in; height: 1in; }
.stamp-box img { width: 100%; height: 100%; display: block; }
```

> **Do not use emoji in this template.** WeasyPrint renders emoji as blank boxes unless an emoji
> font is installed, and the Docker image only installs `fonts-liberation` (`Dockerfile:11`). The
> existing tri-fold template has a `[chart emoji]` at line 345 — that is a pre-existing wart. Do
> not copy it into the new template. The approved mockup already uses plain text.

**Done when:** the template string parses — `Template(TWO_UP_TEMPLATE)` raises no error.

---

### Step 5 — Add the pairing loop to `generate_mailers()`

Add a `layout='trifold'` parameter to the signature (default keeps old behaviour), then branch.

Helper to build one client's data — this is the existing per-client logic from lines 636–677
pulled into a function so both layouts can share it:

```python
def _build_mailer_context(client, valid_sold, num_nearby, filter_settings, mapbox_token, map_size):
    """Build the render context for a single client. map_size is e.g. '500x254'."""
    nearby = find_nearest_sold(
        client['coords'], valid_sold, n=num_nearby,
        client_row=client, filter_settings=filter_settings,
    )
    lat, lon = client['coords']

    markers = f"pin-l+c0392b({lon},{lat})"
    for home in nearby:
        if home.get('coords'):
            h_lat, h_lon = home['coords']
            markers += f",pin-s+27ae60({h_lon},{h_lat})"

    map_url = (
        f"https://api.mapbox.com/styles/v1/mapbox/streets-v12/static/"
        f"{markers}/{lon},{lat},14,0/{map_size}@2x?access_token={mapbox_token}"
    )

    first_name = str(client.get('Primary First', '')).strip()
    last_name  = str(client.get('Primary Last', '')).strip()
    first_name = 'Neighbor' if (not first_name or first_name.lower() == 'nan') else first_name.upper()
    last_name  = '' if last_name.lower() == 'nan' else last_name.upper()

    return {
        'first_name': first_name,
        'last_name':  last_name,
        'address':    str(client.get('Address', '')).strip(),
        'city':       str(client.get('City', 'BAKERSFIELD')).strip().upper(),
        'zip_code':   str(client.get('ZIP', '')).split('.')[0].strip(),
        'nearby':     nearby,
        'map_url':    map_url,
    }
```

The 2-up loop — **pair clients sequentially, two per sheet**:

```python
indicia_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'static', 'usps_indicia.png')
indicia_img  = image_to_base64(indicia_path)
if not indicia_img:
    logger.warning("USPS indicia missing - sheets will render without it",
                   extra={'expected_path': indicia_path})

template  = Template(TWO_UP_TEMPLATE)
map_size  = f"{MAP_REQ_W_PX}x{MAP_REQ_H_PX}"
compact   = num_nearby >= COMPACT_TABLE_THRESHOLD
clients   = list(valid_clients.iterrows())
pdf_files = []

# Walk the client list two at a time: (0,1), (2,3), (4,5) ...
for sheet_idx in range(0, len(clients), 2):
    pair = clients[sheet_idx:sheet_idx + 2]     # 1 or 2 clients; last sheet may be 1

    mailers = [
        _build_mailer_context(c, valid_sold, num_nearby, filter_settings,
                              mapbox_token, map_size)
        for _, c in pair
    ]

    html_out = template.render(
        mailers=mailers, indicia_img=indicia_img, compact=compact,
    )

    sheet_num = (sheet_idx // 2) + 1
    file_path = os.path.join(individual_dir, f"sheet_{sheet_num:03d}.pdf")
    HTML(string=html_out).write_pdf(file_path)
    pdf_files.append(file_path)

    logger.info("Sheet rendered", extra={
        'layout': 'two_up', 'sheet': sheet_num, 'clients_on_sheet': len(pair),
    })

    step += len(pair)
    report(step, total_steps, f'Generated sheet {sheet_num} ({len(pair)} mailers)')
```

Merge filename should differ per layout so the two never collide:

```python
merged_name = 'final_mailers_2up.pdf' if layout == 'two_up' else 'final_mailers_trifold.pdf'
merged_path = os.path.join(output_dir, merged_name)
```

**Watch out for these three things:**

1. **Odd client count.** 25 clients -> 13 sheets, and sheet 13 holds only 1 mailer. The
   `{% if mailers|length > 1 %}` guard in the template leaves the bottom half genuinely blank
   white. Test this explicitly with an odd number.
2. **`step` counting.** The existing progress math at line 602 is
   `total_clients + total_sold + total_clients + 1`. That still works — increment `step` by
   `len(pair)` (clients rendered), not by 1 (sheets), or the progress bar stalls around 50%.
3. **`valid_clients.iterrows()` returns `(index, row)` tuples.** `pair` holds tuples, so unpack
   with `for _, c in pair`. Getting this wrong gives a confusing pandas error.

**Done when:** running with 5 clients produces `sheet_001.pdf` through `sheet_003.pdf`, and
sheet 3's bottom half is blank.

---

### Step 6 — Wire the layout choice through `app.py`

**`/generate` route, near line 254** (where `num_clients` is read):

```python
layout = request.form.get('layout', 'trifold')
if layout not in ('trifold', 'two_up'):
    layout = 'trifold'          # never trust the client; fall back to the safe default
```

**`params` dict, near line 288** — add one key:

```python
'layout': layout,
```

**`run_generation()`, in the `generate_mailers(...)` call near line 127** — add one argument:

```python
layout=params.get('layout', 'trifold'),
```

**Also raise the `num_nearby` cap.** Line 253 currently does `int(request.form.get('num_nearby', 3))`
with no upper bound, so the backend already accepts 7 — only the dropdown limits it. Clamp it
anyway so a hand-crafted POST cannot request 50 rows and blow out the layout:

```python
num_nearby = max(1, min(7, int(request.form.get('num_nearby', 3))))
```

**Done when:** posting `layout=two_up` produces a 2-up ZIP; posting nothing still produces a tri-fold.

---

### Step 7 — Update `templates/index.html`

**7a — Add the layout dropdown.** Put it in the Settings section right before the `num_nearby`
select (~line 188). Reuse the existing `.form-group` / `.form-grid` classes; **no CSS changes needed.**

```html
<div class="form-group">
    <label for="layout">Mailer Layout</label>
    <select id="layout" name="layout">
        <option value="trifold" selected>Tri-Fold — 8.5" x 11" (1 per page)</option>
        <option value="two_up">2-Up A4 — 8.27" x 11.69" (2 per page)</option>
    </select>
    <span class="input-hint">2-Up prints two mailers per sheet — cut in half after printing</span>
</div>
```

**7b — Extend `num_nearby` to 7** (lines 189–193):

```html
<select id="num_nearby" name="num_nearby">
    <option value="3" selected>3 properties</option>
    <option value="4">4 properties</option>
    <option value="5">5 properties</option>
    <option value="6">6 properties</option>
    <option value="7">7 properties</option>
</select>
```

**7c — Fix the hardcoded result text at line 389.** It currently always says "tri-fold":

```javascript
// BEFORE
let summary = `Generated ${data.pdf_count} tri-fold mailer PDFs (8.5" x 11").`;

// AFTER
const layout = document.getElementById('layout').value;
let summary = layout === 'two_up'
    ? `Generated ${data.pdf_count} A4 sheets (8.27" x 11.69"), 2 mailers per sheet.`
    : `Generated ${data.pdf_count} tri-fold mailer PDFs (8.5" x 11").`;
```

**7d — Soften the page header** (lines 17–18), since it now does both layouts:

```html
<h1>Mailer Generator</h1>
<p class="subtitle">Tri-Fold 8.5" x 11" &nbsp;|&nbsp; 2-Up A4 8.27" x 11.69"</p>
```

**7e — Grey out the 3 banner uploads in 2-up mode** (Decision 2, section 8). Add the JS listener
shown in section 8 alongside the existing toggle listeners near line 464.

**Done when:** the form shows both dropdowns, the result text matches the chosen layout, and
selecting "2-Up A4" visibly disables the three banner upload fields with a hint.

---

## 6. How to verify it actually works

**Do not trust "it looks right in the browser."** The mockup is HTML; the deliverable is a PDF
rendered by WeasyPrint, which is a different engine. Measure the real output.

### 6.1 Measure the PDF page size

Save as `verify_2up.py` (throwaway, do not commit):

```python
from pypdf import PdfReader

r = PdfReader('output/final_mailers_2up.pdf')
for i, page in enumerate(r.pages[:3]):
    box  = page.mediabox
    w_in = float(box.width)  / 72     # PDF points -> inches
    h_in = float(box.height) / 72
    print(f"page {i+1}: {w_in:.2f}in x {h_in:.2f}in")
```

Expected: `8.27in x 11.69in` on every page (tolerance +/- 0.02in for rounding).

### 6.2 Manual print check — the one that actually matters

1. Generate with **3 clients** (odd count -> 2 sheets, last half blank).
2. Print sheet 1 at **100% scale / "Actual size"** — *not* "Fit to page", which silently shrinks it.
3. With a ruler, confirm:
   - [ ] The indicia measures **1.0in x 1.0in**
   - [ ] Cutting at the 5.845in midpoint does not clip either mailer
   - [ ] The map has visible white margin above the cut line (Joy's request #8)
   - [ ] Both the red home pin and all green sale pins are visible — **nothing cropped** (section 4.2)
   - [ ] Property table text is solid black and clearly readable
   - [ ] Header bar and table header are `#2E72F9` with `#D2F249` text
   - [ ] The caption reads "Recent Public Records Sales" — no "Mapbox" anywhere
   - [ ] No return address anywhere on the sheet
   - [ ] Sheet 2's bottom half is completely blank

### 6.3 Regression — prove the tri-fold still works

This is the highest-risk part of the job. Joy still uses the old layout.

```bash
python app.py          # terminal 1  (note: test_app.py expects port 5001)
python test_app.py     # terminal 2
```

All 14 existing test groups must still pass. Then generate one tri-fold job through the UI and
confirm the PDF is still 8.5in x 11in with three panels.

### 6.4 Add two new tests to `test_app.py`

Follow the existing `submit_generate()` / `poll_job()` helper style:

```python
def test_generate_two_up():
    print("\n[15] 2-Up A4 layout - 3 clients (odd count)")
    files = {"client_csv": ("clients.csv", open(CLIENT_CSV, "rb"), "text/csv"),
             "sold_csv":   ("sold.csv",    open(SOLD_CSV,   "rb"), "text/csv")}
    data  = {"num_clients": "3", "num_nearby": "5", "layout": "two_up"}
    r = requests.post(f"{BASE}/generate", files=files, data=data)
    check("Returns 202", r.status_code == 202)
    result = poll_job(r.json()["job_id"])
    check("Status = done",         result["status"] == "done", result.get("message", ""))
    check("3 clients -> 2 sheets", result.get("pdf_count") == 2, f"got {result.get('pdf_count')}")
    return r.json()["job_id"]

def test_two_up_page_size(job_id):
    print("\n[16] 2-Up PDF pages are A4")
    # download the ZIP, extract final_mailers_2up.pdf,
    # assert mediabox is ~595 x 842 pt  (8.27in x 11.69in at 72 dpi)
```

---

## 7. Risks and gotchas

| Risk | Likelihood | What happens | Mitigation |
|---|---|---|---|
| **Map pins cropped by `object-fit: cover`** | High if section 4.2 is skipped | Silent — bad mailers printed at scale | Fixed map height + matched Mapbox ratio; verify visually in 6.2 |
| **USPS changes the right-panel width** | Confirmed pending | Rework | All dimensions in one constants block (Step 2) |
| **Emoji renders as blank boxes** | Medium | Ugly mailer | Plain text only in the new template |
| **Tri-fold regression** | Medium | Joy's current workflow breaks | New template is additive; `test_app.py` must pass |
| **Odd client count crashes or drops a client** | Medium | Missing mailer | `{% if %}` guard + explicit 3-client test |
| **Progress bar stalls at 50%** | Medium | Looks hung to Joy | Increment `step` by `len(pair)`, not 1 |
| **Indicia PNG missing in production** | Low | Mail rejected by USPS | Commit to `static/`; log a warning if absent |
| **`saved_data/` wiped on redeploy** | Pre-existing | Saved sold list lost | Out of scope — already flagged in `new-add-ons.md:114` |

---

## 8. Decisions — locked in, no client sign-off needed

**Decision 1 — Keep the tri-fold layout, or replace it? -> KEEP BOTH.**

Both layouts live behind the dropdown (that is what this plan builds). It costs about 15 extra
lines, and it means the worst case for a bug in the new code is "switch the dropdown back"
rather than "can't send mail." Replacing outright would save almost nothing and removes the
fallback.

**Decision 2 — What happens to the three banner uploads? -> GREY THEM OUT IN 2-UP MODE.**

The tri-fold uses `top_banner`, `bottom_banner`, and `right_side_image` (`app.py:230–250`). The
2-up mockup has no banner areas at all, so in 2-up mode those three uploads would otherwise be
silently ignored — a user could upload an image, submit, and never see it anywhere, with no
explanation. That is the worse failure mode compared to the small cost of disabling the fields,
so: add a JS toggle that greys out all three upload fields with the hint *"Not used in the 2-Up
layout"* whenever `layout=two_up` is selected (~10 lines in `templates/index.html`, alongside
the existing `use_saved_sold` toggle pattern at line 464).

```javascript
// templates/index.html - add alongside the existing toggle listeners (~line 464)
document.getElementById('layout').addEventListener('change', function() {
    const isTwoUp = this.value === 'two_up';
    ['top_banner', 'bottom_banner', 'right_side_image'].forEach(id => {
        const input = document.getElementById(id);
        input.disabled = isTwoUp;
        const wrapper = input.closest('.form-group');
        wrapper.style.opacity = isTwoUp ? '0.5' : '1';
        let hint = wrapper.querySelector('.two-up-hint');
        if (isTwoUp && !hint) {
            hint = document.createElement('span');
            hint.className = 'input-hint two-up-hint';
            hint.textContent = 'Not used in the 2-Up layout';
            wrapper.appendChild(hint);
        } else if (hint) {
            hint.remove();
        }
    });
});
```

Disabled file inputs are not submitted with the form, so `app.py` needs no change for this — the
three `request.files` keys are simply absent when in 2-up mode, which the existing
`if 'top_banner' in request.files:` guards (`app.py:234, 240, 246`) already handle correctly.

---

## 9. Defaults locked in without needing Joy's input

No further client questions before coding — these are reasonable defaults, each a one-line
change later if wrong:

1. **Price colour stays green** (`#27ae60`). Joy's "bold black" request reads as being about the
   address/bed-bath/sqft columns that were washing out — green on the price is an intentional
   accent (reads as "sold"), not the illegible text he flagged, so it is left alone.
2. **Header text stays "GEBARAH REAL ESTATE GROUP."** No instruction to change it.
3. **Return address stays removed**, per his explicit Jul 28 message.
4. **Default row count stays 3** (matches the "3-ROW (APPROVED)" mockup label); dropdown simply
   extends up to 7 as an option.

---

## 10. Build order summary

| # | Step | File(s) | Est. |
|---|---|---|---|
| 1 | Commit indicia to `static/` | `static/usps_indicia.png` | 5 min |
| 2 | Constants block | `mailer_generator.py` | 15 min |
| 3 | Logging | `mailer_generator.py` | 20 min |
| 4 | `TWO_UP_TEMPLATE` | `mailer_generator.py` | 1.5 hr |
| 5 | Pairing loop + `_build_mailer_context()` | `mailer_generator.py` | 1.5 hr |
| 6 | Route plumbing + clamp `num_nearby` | `app.py` | 30 min |
| 7 | Form dropdowns + result text | `templates/index.html` | 30 min |
| 8 | Verify (section 6) incl. physical print test | `test_app.py` + ruler | 1 hr |
| 9 | Apply Joy's USPS width when it arrives | Step 2 constants | 5 min |

**Total: ~6 hours.** Agreed price **$85**.

---

## 11. Rollback

Every change is additive — the tri-fold path is untouched. If the 2-up layout misbehaves in
production, Joy selects "Tri-Fold" in the dropdown and is immediately back to today's behaviour,
no deploy required. For a full revert, the work should land on a branch:

```bash
git checkout -b feature/two-up-a4-layout
```

That way `git checkout main` restores the current state instantly.
