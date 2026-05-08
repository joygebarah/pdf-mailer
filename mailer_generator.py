"""
Mailer Generator Module - Extracted PDF generation logic
For Flask web app version of the Trifold Mailer Generator
Uses persistent geocoding cache (geocoding_cache_mapbox.json)
"""

import pandas as pd
import os
import json
import threading
import requests
import base64
from geopy.distance import geodesic
from jinja2 import Template
from pypdf import PdfWriter
from weasyprint import HTML


# Thread-safe lock for cache file writes
_cache_lock = threading.Lock()

# Persistent cache path (lives next to this module so it persists across jobs)
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'geocoding_cache_mapbox.json')


# --- TRI-FOLD TEMPLATE (8.5" x 11" Letter) ---
# Identical to mailer_app_trifold.py
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <style>
        @page { 
            size: 8.5in 11in; 
            margin: 0; 
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body { 
            font-family: 'Arial', 'Helvetica', sans-serif;
            width: 8.5in;
            height: 11in;
            background: #ffffff;
            color: #2c2c2c;
        }
        
        .panel {
            width: 100%;
            overflow: hidden;
            position: relative;
        }
        
        .panel-address {
            height: 3.75in;
        }
        
        .panel-content {
            height: 3.65in;
        }
        
        .panel-bottom {
            height: 3.6in;
        }
        
        .panel.panel-address {
            background: #ffffff;
            display: flex;
            flex-direction: column;
            padding: 0.25in;
        }
        
        .mini-header {
            background: linear-gradient(135deg, #8B4513 0%, #A0522D 50%, #D4AF37 100%);
            color: white;
            padding: 8px 15px;
            text-align: center;
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 2px;
            border-radius: 4px;
            margin-bottom: 0.15in;
            position: relative;
        }
        
        .address-container {
            margin-top: 1.8in;
            margin-left: 0.875in;
            width: 4in;
            height: 1in;
            padding: 10px 15px;
        }
        
        .right-side-image {
            position: absolute;
            top: 1.8in;
            right: 0.4in;
            width: 1.1in;
            height: 1.1in;
            border-radius: 6px;
            overflow: hidden;
            border: 2px solid #D4AF37;
        }
        
        .right-side-image img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .address-line {
            font-size: 12pt;
            line-height: 1.5;
            color: #000;
        }
        
        .address-name {
            font-weight: bold;
            text-transform: uppercase;
        }
        
        .return-address {
            position: absolute;
            top: 6px;
            right: 15px;
            font-size: 7pt;
            color: #ffd700;
            text-align: right;
            line-height: 1.3;
            max-width: 180px;
        }
        
        .panel.panel-content {
            background: #ffffff;
            padding: 0.15in 0.25in;
            display: flex;
            flex-direction: column;
        }
        
        .top-banner {
            width: 100%;
            height: 0.75in;
            background: #f5f5f5;
            border: none;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            margin-bottom: 0.1in;
        }
        
        .top-banner img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .top-banner.placeholder {
            color: #999;
            font-size: 11px;
            font-style: italic;
        }
        
        .content-row {
            display: flex;
            gap: 0.15in;
            flex: 1;
            max-height: 2.6in;
        }
        
        .map-section {
            width: 3.2in;
            display: flex;
            flex-direction: column;
        }
        
        .map-box {
            flex: 1;
            border-radius: 6px;
            overflow: hidden;
            border: 2px solid #D4AF37;
            background: #f8f8f8;
        }
        
        .map-box img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .map-legend {
            font-size: 7pt;
            color: #666;
            text-align: center;
            margin-top: 3px;
        }
        
        .legend-red { color: #c0392b; font-weight: bold; }
        .legend-green { color: #27ae60; font-weight: bold; }
        
        .table-section {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        .section-title {
            font-size: 9pt;
            color: #8B4513;
            font-weight: bold;
            padding-bottom: 4px;
            border-bottom: 1.5px solid #D4AF37;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }
        
        .property-table {
            font-size: 7.5pt;
            width: 100%;
            border-collapse: collapse;
        }
        
        .property-table th {
            background: #8B4513;
            color: white;
            padding: 4px 6px;
            text-align: left;
            font-weight: bold;
        }
        
        .property-table td {
            padding: 4px 6px;
            border-bottom: 1px solid #eee;
        }
        
        .property-table tr:nth-child(even) {
            background: #f9f9f9;
        }
        
        .price-cell {
            color: #27ae60;
            font-weight: bold;
        }
        
        .panel.panel-bottom {
            background: #ffffff;
            padding: 0.15in 0.25in;
            display: flex;
            flex-direction: column;
        }
        
        .bottom-banner {
            width: 100%;
            flex: 1;
            background: #f5f5f5;
            border: none;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }
        
        .bottom-banner img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        
        .bottom-banner.placeholder {
            color: #999;
            font-size: 14px;
            font-style: italic;
        }
        
        .mini-footer {
            margin-top: 0.1in;
            background: linear-gradient(135deg, #2c2c2c 0%, #4a4a4a 100%);
            color: white;
            padding: 8px 15px;
            border-radius: 4px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 8pt;
        }
        
        .mini-footer .agent-name {
            color: #D4AF37;
            font-weight: bold;
            font-size: 10pt;
        }
        
        .mini-footer .contact-info {
            color: #ccc;
        }
    </style>
</head>
<body>
    <!-- PANEL 1: ADDRESS (TOP) -->
    <div class="panel panel-address">
        <div class="mini-header">GEBARAH REAL ESTATE GROUP</div>
        
        <div class="address-container">
            <div class="address-line address-name">{{ first_name }} {{ last_name }}</div>
            <div class="address-line">{{ address }}</div>
            <div class="address-line">{{ city }}, CA {{ zip_code }}</div>
        </div>
        
        {% if right_side_img %}
        <div class="right-side-image">
            <img src="{{ right_side_img }}" alt="Right Side Image">
        </div>
        {% endif %}
    </div>
    
    <!-- PANEL 2: MAIN CONTENT (MIDDLE) -->
    <div class="panel panel-content">
        {% if top_banner_img %}
        <div class="top-banner">
            <img src="{{ top_banner_img }}" alt="Top Banner">
        </div>
        {% else %}
        <div class="top-banner placeholder">
            [Top Banner - Upload your custom Canva design]
        </div>
        {% endif %}
        
        <div class="content-row">
            <div class="map-section">
                <div class="map-box">
                    <img src="{{ map_url }}" alt="Neighborhood Map">
                </div>
                <div class="map-legend">
                    <span class="legend-red">● Your Home</span> &nbsp;|&nbsp; 
                    <span class="legend-green">● Recent Sales</span>
                </div>
            </div>
            
            <div class="table-section">
                <div class="section-title">📊 Recent Nearby Sales</div>
                <table class="property-table">
                    <tr>
                        <th>Address</th>
                        <th>Price</th>
                        <th>Bed/Bath</th>
                        <th>Sq Ft</th>
                    </tr>
                    {% for property in nearby %}
                    <tr>
                        <td>{{ property['Address'][:25] }}{% if property['Address']|length > 25 %}...{% endif %}</td>
                        <td class="price-cell">${{ "{:,.0f}".format(property['Purchase Amt'] / 1000) }}k</td>
                        <td>{{ property['Beds'] }}/{{ property['Baths'] }}</td>
                        <td>{{ "{:,.0f}".format(property['Sq Ft']) }}</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>
    </div>
    
    <!-- PANEL 3: BOTTOM BANNER (BOTTOM) -->
    <div class="panel panel-bottom">
        {% if bottom_banner_img %}
        <div class="bottom-banner">
            <img src="{{ bottom_banner_img }}" alt="Bottom Banner">
        </div>
        {% else %}
        <div class="bottom-banner placeholder">
            [Bottom Banner - Upload your custom Canva design / Call-to-Action]
        </div>
        {% endif %}
    </div>
</body>
</html>
"""


# ─── Helpers ───────────────────────────────────────────────────────────────────

def image_to_base64(image_path):
    """Convert an image file to base64 data URI"""
    if not image_path or not os.path.exists(image_path):
        return None

    ext = os.path.splitext(image_path)[1].lower()
    mime_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    mime_type = mime_types.get(ext, 'image/png')

    with open(image_path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('utf-8')

    return f"data:{mime_type};base64,{encoded}"


def load_cache():
    """Load the persistent geocoding cache (thread-safe read)"""
    with _cache_lock:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
    return {}


def save_cache(cache):
    """Persist the geocoding cache to disk (thread-safe write)"""
    with _cache_lock:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)


def geocode_address(row, mapbox_token, cache):
    """Geocode an address using Mapbox API. Returns [lat, lon] or None."""
    address = str(row['Address']).strip()
    city = str(row.get('City', 'Bakersfield')).strip()
    zip_code = str(row.get('ZIP', '')).split('.')[0].strip()
    full_address = f"{address}, {city}, CA {zip_code}"

    if full_address in cache:
        return cache[full_address]

    try:
        url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{requests.utils.quote(full_address)}.json"
        params = {
            'access_token': mapbox_token,
            'limit': 1,
            'country': 'US'
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get('features'):
            coords_long_lat = data['features'][0]['center']  # [longitude, latitude]
            coords = [coords_long_lat[1], coords_long_lat[0]]  # -> [lat, lon]
            cache[full_address] = coords
            save_cache(cache)
            return coords
    except Exception as e:
        print(f"Geocoding failed for {address}: {e}")

    return None


def find_nearest_sold(client_coords, sold_df, n=3, client_row=None, filter_settings=None):
    """Find n nearest sold properties, with optional comparable filtering by sqft/age/beds."""
    sold_pool = sold_df.copy()
    sold_pool['distance'] = sold_pool['coords'].apply(
        lambda x: geodesic(client_coords, x).miles if x else float('inf')
    )
    candidates = sold_pool[sold_pool['distance'] > 0.005].sort_values('distance')

    if filter_settings and client_row is not None:
        sqft_pct  = filter_settings.get('sqft_pct')
        age_years = filter_settings.get('age_years')
        beds_diff = filter_settings.get('beds_diff')

        client_sqft = pd.to_numeric(client_row.get('Sq Ft'), errors='coerce')
        client_yr   = pd.to_numeric(client_row.get('Yr Built'), errors='coerce')
        client_beds = pd.to_numeric(client_row.get('Beds'), errors='coerce')

        filtered = candidates.copy()

        if sqft_pct and pd.notna(client_sqft) and client_sqft > 0 and 'Sq Ft' in filtered.columns:
            filtered = filtered[
                filtered['Sq Ft'].apply(
                    lambda x: abs(pd.to_numeric(x, errors='coerce') - client_sqft) / client_sqft <= sqft_pct / 100
                    if pd.notna(pd.to_numeric(x, errors='coerce')) else False
                )
            ]

        if age_years and pd.notna(client_yr) and 'Yr Built' in filtered.columns:
            filtered = filtered[
                filtered['Yr Built'].apply(
                    lambda x: abs(pd.to_numeric(x, errors='coerce') - client_yr) <= age_years
                    if pd.notna(pd.to_numeric(x, errors='coerce')) else False
                )
            ]

        if beds_diff is not None and pd.notna(client_beds) and 'Beds' in filtered.columns:
            filtered = filtered[
                filtered['Beds'].apply(
                    lambda x: abs(pd.to_numeric(x, errors='coerce') - client_beds) <= beds_diff
                    if pd.notna(pd.to_numeric(x, errors='coerce')) else False
                )
            ]

        if len(filtered) >= n:
            return filtered.head(n).to_dict('records')

        # Fallback: use filtered matches, fill remaining slots from unfiltered by distance
        filtered_results = filtered.head(n).to_dict('records')
        used_addresses = {r['Address'] for r in filtered_results}
        fallback = candidates[~candidates['Address'].isin(used_addresses)].head(n - len(filtered_results))
        return filtered_results + fallback.to_dict('records')

    return candidates.head(n).to_dict('records')


# ─── Main Generation ──────────────────────────────────────────────────────────

def generate_mailers(
    client_csv_path,
    sold_csv_path,
    output_dir,
    mapbox_token,
    top_banner_path=None,
    bottom_banner_path=None,
    right_side_image_path=None,
    num_nearby=3,
    num_clients='all',
    progress_callback=None,
    filter_settings=None
):
    """
    Generate mailer PDFs from CSV data (matches mailer_app_trifold.py logic).

    progress_callback(current_step, total_steps, message) is called frequently
    so the web UI can poll for live updates.

    Returns dict: { success, pdf_files, merged_pdf, skipped, error }
    """
    result = {
        'success': False,
        'pdf_files': [],
        'merged_pdf': None,
        'skipped': [],
        'error': None
    }

    def report(current, total, msg):
        if progress_callback:
            progress_callback(current, total, msg)

    try:
        # ── Output dirs ──
        individual_dir = os.path.join(output_dir, 'individual')
        os.makedirs(individual_dir, exist_ok=True)

        # ── Persistent cache (shared across all jobs) ──
        cache = load_cache()

        # ── Load banner images as base64 ──
        top_banner_img = image_to_base64(top_banner_path) if top_banner_path else None
        bottom_banner_img = image_to_base64(bottom_banner_path) if bottom_banner_path else None
        right_side_img = image_to_base64(right_side_image_path) if right_side_image_path else None

        # ── Load CSV data ──
        report(0, 1, 'Loading CSV data...')
        df_clients = pd.read_csv(client_csv_path)
        df_sold = pd.read_csv(sold_csv_path)

        # Clean garbage rows (same as trifold app)
        df_clients = df_clients[df_clients['Address'].str.contains("The information", na=False) == False]
        df_sold = df_sold[df_sold['Address'].str.contains("The information", na=False) == False]

        # Limit clients
        if num_clients != 'all' and num_clients != '':
            try:
                limit = int(num_clients)
                df_clients = df_clients.head(limit)
            except Exception:
                pass

        total_clients = len(df_clients)
        total_sold = len(df_sold)
        # Steps: geocode clients + geocode sold + generate PDFs + merge
        total_steps = total_clients + total_sold + total_clients + 1
        step = 0

        # ── Geocode clients ──
        client_coords = []
        for i, (_, row) in enumerate(df_clients.iterrows()):
            coords = geocode_address(row, mapbox_token, cache)
            client_coords.append(coords)
            step += 1
            report(step, total_steps, f'Geocoding client {i+1}/{total_clients}')
        df_clients['coords'] = client_coords

        # ── Geocode sold properties ──
        sold_coords = []
        for i, (_, row) in enumerate(df_sold.iterrows()):
            coords = geocode_address(row, mapbox_token, cache)
            sold_coords.append(coords)
            step += 1
            report(step, total_steps, f'Geocoding sold property {i+1}/{total_sold}')
        df_sold['coords'] = sold_coords

        valid_clients = df_clients.dropna(subset=['coords']).copy()
        valid_sold = df_sold.dropna(subset=['coords']).copy()

        skipped_clients = total_clients - len(valid_clients)
        if skipped_clients > 0:
            result['skipped'] = [{'count': skipped_clients, 'reason': 'Geocoding failed'}]

        # ── Generate PDFs ──
        template = Template(HTML_TEMPLATE)
        pdf_files = []

        for idx, (index, client) in enumerate(valid_clients.iterrows()):
            nearby = find_nearest_sold(client['coords'], valid_sold, n=num_nearby, client_row=client, filter_settings=filter_settings)
            lat, lon = client['coords']

            # Mapbox Static Map URL (same as trifold app)
            markers = f"pin-l+c0392b({lon},{lat})"
            if nearby:
                for home in nearby:
                    if home.get('coords'):
                        h_lat, h_lon = home['coords']
                        markers += f",pin-s+27ae60({h_lon},{h_lat})"

            map_url = (
                f"https://api.mapbox.com/styles/v1/mapbox/streets-v12/static/"
                f"{markers}/{lon},{lat},14,0/500x400@2x?access_token={mapbox_token}"
            )

            # Client info (same logic as trifold app)
            first_name = str(client.get('Primary First', '')).strip()
            last_name = str(client.get('Primary Last', '')).strip()
            if not first_name or first_name.lower() == 'nan':
                first_name = 'Neighbor'
            else:
                first_name = first_name.upper()
            if last_name.lower() == 'nan':
                last_name = ''
            else:
                last_name = last_name.upper()

            address = str(client.get('Address', '')).strip()
            city = str(client.get('City', 'BAKERSFIELD')).strip().upper()
            zip_code = str(client.get('ZIP', '')).split('.')[0].strip()

            html_out = template.render(
                first_name=first_name,
                last_name=last_name,
                address=address,
                city=city,
                zip_code=zip_code,
                nearby=nearby,
                map_url=map_url,
                top_banner_img=top_banner_img,
                bottom_banner_img=bottom_banner_img,
                right_side_img=right_side_img
            )

            file_path = os.path.join(individual_dir, f"mailer_trifold_{idx}.pdf")
            HTML(string=html_out).write_pdf(file_path)
            pdf_files.append(file_path)

            step += 1
            report(step, total_steps, f'Generated PDF {idx+1}/{len(valid_clients)}')

        # ── Merge PDFs ──
        report(step, total_steps, 'Merging all PDFs...')
        if pdf_files:
            merger = PdfWriter()
            for pdf in pdf_files:
                merger.append(pdf)
            merged_path = os.path.join(output_dir, 'final_mailers_trifold.pdf')
            with open(merged_path, 'wb') as f:
                merger.write(f)
            result['merged_pdf'] = merged_path
        step += 1
        report(step, total_steps, 'Complete!')

        result['success'] = True
        result['pdf_files'] = pdf_files

    except Exception as e:
        import traceback
        result['error'] = str(e)
        result['traceback'] = traceback.format_exc()

    return result
