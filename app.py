"""
Real Estate Mailer Generator - Flask Web App
Asynchronous background processing with progress tracking
"""

import csv
import io
import os
import sys
import glob
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from flask import Flask, render_template, request, send_file, jsonify, Response
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# --- WINDOWS GTK PATCH (same as mailer_app_trifold.py) ---
MSYS2_BIN_PATH = r'D:\msys2\ucrt64\bin'
if sys.platform == 'win32' and os.path.exists(MSYS2_BIN_PATH):
    os.add_dll_directory(MSYS2_BIN_PATH)

from mailer_generator import generate_mailers
from analyzer import run_full_analysis, parse_csv

# Suppress Flask's access log spam from JS status polling every 2s
import logging
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-in-production')

# Allowed file extensions
ALLOWED_CSV = {'csv'}
ALLOWED_IMAGES = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# In-memory job stores
jobs = {}           # mailer jobs
analyze_jobs = {}   # analyzer jobs

# Auto-cleanup: remove finished jobs older than 10 minutes
JOB_TTL_SECONDS = 600

# Max total temp disk usage before refusing new jobs (2 GB)
# This only blocks NEW jobs when old ones haven't been cleaned up yet.
# A single job (even 1000+ mailers) always starts from ~0 and won't hit this.
MAX_TEMP_USAGE_BYTES = 2 * 1024 * 1024 * 1024

SAVED_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'saved_data')


def _parse_positive(val):
    try:
        v = float(val)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions


def cleanup_old_jobs():
    """Remove jobs older than JOB_TTL_SECONDS"""
    now = time.time()
    expired = [jid for jid, j in jobs.items()
               if j.get('finished_at') and (now - j['finished_at']) > JOB_TTL_SECONDS]
    for jid in expired:
        job = jobs.pop(jid, None)
        if job and job.get('job_dir'):
            shutil.rmtree(job['job_dir'], ignore_errors=True)


def cleanup_orphaned_temp_dirs():
    """Remove mailer_job_* temp dirs that aren't tracked in the jobs dict.
    This handles orphans left behind after a server restart/redeploy."""
    tracked_dirs = {j.get('job_dir') for j in jobs.values() if j.get('job_dir')}
    temp_root = tempfile.gettempdir()
    for d in glob.glob(os.path.join(temp_root, 'mailer_job_*')):
        if os.path.isdir(d) and d not in tracked_dirs:
            shutil.rmtree(d, ignore_errors=True)


def get_temp_usage_bytes():
    """Calculate total disk usage of all tracked job directories."""
    total = 0
    for j in jobs.values():
        job_dir = j.get('job_dir')
        if job_dir and os.path.isdir(job_dir):
            for dirpath, dirnames, filenames in os.walk(job_dir):
                for f in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
    return total


# --- Startup cleanup: wipe orphaned temp dirs from previous deploys ---
cleanup_orphaned_temp_dirs()


def run_generation(job_id, job_dir, params):
    """Background worker that generates mailers and updates job progress"""
    job = jobs[job_id]

    def progress_callback(current, total, message):
        job['progress'] = current
        job['total'] = total
        job['message'] = message

    try:
        job['status'] = 'running'
        job['message'] = 'Starting generation...'

        output_dir = os.path.join(job_dir, 'output')
        os.makedirs(output_dir, exist_ok=True)

        result = generate_mailers(
            client_csv_path=params['client_csv_path'],
            sold_csv_path=params['sold_csv_path'],
            output_dir=output_dir,
            mapbox_token=params['mapbox_token'],
            top_banner_path=params.get('top_banner_path'),
            bottom_banner_path=params.get('bottom_banner_path'),
            right_side_image_path=params.get('right_side_path'),
            num_nearby=params.get('num_nearby', 3),
            num_clients=params.get('num_clients', 'all'),
            progress_callback=progress_callback,
            filter_settings=params.get('filter_settings'),
        )

        if not result['success']:
            job['status'] = 'failed'
            job['message'] = result.get('error', 'Generation failed')
            job['finished_at'] = time.time()
            return

        # Create ZIP
        job['message'] = 'Creating ZIP archive...'
        zip_path = os.path.join(job_dir, 'mailers.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for pdf_path in result['pdf_files']:
                arcname = os.path.join('individual', os.path.basename(pdf_path))
                zipf.write(pdf_path, arcname)
            if result['merged_pdf'] and os.path.exists(result['merged_pdf']):
                zipf.write(result['merged_pdf'], os.path.basename(result['merged_pdf']))

        job['status'] = 'done'
        job['zip_path'] = zip_path
        job['pdf_count'] = len(result['pdf_files'])
        job['skipped_count'] = len(result.get('skipped', []))
        job['message'] = f"Done! Generated {len(result['pdf_files'])} mailers."
        job['progress'] = job['total']
        job['finished_at'] = time.time()

    except Exception as e:
        import traceback
        job['status'] = 'failed'
        job['message'] = str(e)
        job['traceback'] = traceback.format_exc()
        job['finished_at'] = time.time()


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return render_template('home.html')


@app.route('/mailer')
def index():
    default_mapbox_token = os.getenv('MAPBOX_TOKEN', '')
    return render_template('index.html', default_mapbox_token=default_mapbox_token)


@app.route('/generate', methods=['POST'])
def generate():
    """Accept uploads, start a background job, return job_id immediately"""
    cleanup_old_jobs()

    job_id = str(uuid.uuid4())
    job_dir = tempfile.mkdtemp(prefix='mailer_job_')
    uploads_dir = os.path.join(job_dir, 'uploads')
    os.makedirs(uploads_dir, exist_ok=True)

    try:
        # ── Validate + save client CSV ──
        if 'client_csv' not in request.files or request.files['client_csv'].filename == '':
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Client list CSV is required'}), 400

        client_csv = request.files['client_csv']
        if not allowed_file(client_csv.filename, ALLOWED_CSV):
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Client file must be a CSV'}), 400

        client_csv_path = os.path.join(uploads_dir, secure_filename(client_csv.filename))
        client_csv.save(client_csv_path)

        # ── Sold CSV: uploaded file OR saved master list ──
        use_saved_sold = request.form.get('use_saved_sold') == 'on'
        if use_saved_sold:
            master_path = os.path.join(SAVED_DATA_DIR, 'master_sold.csv')
            if not os.path.exists(master_path):
                shutil.rmtree(job_dir, ignore_errors=True)
                return jsonify({'error': 'No saved sold list found — please upload your master sold list first.'}), 400
            sold_csv_path = os.path.join(uploads_dir, 'master_sold.csv')
            shutil.copy(master_path, sold_csv_path)
        else:
            if 'sold_csv' not in request.files or request.files['sold_csv'].filename == '':
                shutil.rmtree(job_dir, ignore_errors=True)
                return jsonify({'error': 'Sold homes CSV is required (or enable "Use saved sold list")'}), 400
            sold_csv = request.files['sold_csv']
            if not allowed_file(sold_csv.filename, ALLOWED_CSV):
                shutil.rmtree(job_dir, ignore_errors=True)
                return jsonify({'error': 'Sold homes file must be a CSV'}), 400
            sold_csv_path = os.path.join(uploads_dir, secure_filename(sold_csv.filename))
            sold_csv.save(sold_csv_path)

        top_banner_path = None
        bottom_banner_path = None
        right_side_path = None

        if 'top_banner' in request.files:
            f = request.files['top_banner']
            if f.filename != '' and allowed_file(f.filename, ALLOWED_IMAGES):
                top_banner_path = os.path.join(uploads_dir, 'top_' + secure_filename(f.filename))
                f.save(top_banner_path)

        if 'bottom_banner' in request.files:
            f = request.files['bottom_banner']
            if f.filename != '' and allowed_file(f.filename, ALLOWED_IMAGES):
                bottom_banner_path = os.path.join(uploads_dir, 'bottom_' + secure_filename(f.filename))
                f.save(bottom_banner_path)

        if 'right_side_image' in request.files:
            f = request.files['right_side_image']
            if f.filename != '' and allowed_file(f.filename, ALLOWED_IMAGES):
                right_side_path = os.path.join(uploads_dir, 'right_' + secure_filename(f.filename))
                f.save(right_side_path)

        mapbox_token = request.form.get('mapbox_token', os.getenv('MAPBOX_TOKEN', ''))
        num_nearby = int(request.form.get('num_nearby', 3))
        num_clients = request.form.get('num_clients', 'all')

        sqft_pct        = _parse_positive(request.form.get('sqft_pct'))
        age_years       = _parse_positive(request.form.get('age_years'))
        beds_diff       = _parse_positive(request.form.get('beds_diff'))
        lot_size_pct    = _parse_positive(request.form.get('lot_size_pct'))
        match_prop_type = request.form.get('match_prop_type') == 'on'
        filter_settings = {
            'sqft_pct': sqft_pct, 'age_years': age_years, 'beds_diff': beds_diff,
            'lot_size_pct': lot_size_pct, 'match_prop_type': match_prop_type,
        } if any([sqft_pct, age_years, beds_diff, lot_size_pct, match_prop_type]) else None

        if not mapbox_token:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Mapbox API token is required'}), 400

        # ── Check disk usage cap ──
        if get_temp_usage_bytes() >= MAX_TEMP_USAGE_BYTES:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Server is busy — too many pending jobs. Please try again in a few minutes.'}), 503

        # ── Create job record ──
        jobs[job_id] = {
            'status': 'queued',
            'progress': 0,
            'total': 1,
            'message': 'Job queued...',
            'job_dir': job_dir,
            'zip_path': None,
            'pdf_count': 0,
            'skipped_count': 0,
            'finished_at': None,
        }

        params = {
            'client_csv_path': client_csv_path,
            'sold_csv_path': sold_csv_path,
            'mapbox_token': mapbox_token,
            'top_banner_path': top_banner_path,
            'bottom_banner_path': bottom_banner_path,
            'right_side_path': right_side_path,
            'num_nearby': num_nearby,
            'num_clients': num_clients,
            'filter_settings': filter_settings,
        }

        # ── Launch background thread ──
        thread = threading.Thread(target=run_generation, args=(job_id, job_dir, params), daemon=True)
        thread.start()

        return jsonify({'job_id': job_id}), 202

    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500


@app.route('/status/<job_id>')
def job_status(job_id):
    """Poll endpoint — returns current progress of a background job"""
    # Piggyback cleanup on frequent poll calls
    cleanup_old_jobs()

    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    return jsonify({
        'status': job['status'],
        'progress': job['progress'],
        'total': job['total'],
        'message': job['message'],
        'pdf_count': job.get('pdf_count', 0),
        'skipped_count': job.get('skipped_count', 0),
    })


@app.route('/download/<job_id>')
def download(job_id):
    """Download the finished ZIP for a completed job"""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if job['status'] != 'done':
        return jsonify({'error': 'Job not finished yet'}), 400
    if not job.get('zip_path') or not os.path.exists(job['zip_path']):
        return jsonify({'error': 'ZIP file not found'}), 404

    response = send_file(
        job['zip_path'],
        mimetype='application/zip',
        as_attachment=True,
        download_name='mailers.zip'
    )

    # Cleanup after download
    @response.call_on_close
    def cleanup():
        j = jobs.pop(job_id, None)
        if j and j.get('job_dir'):
            shutil.rmtree(j['job_dir'], ignore_errors=True)

    return response


@app.route('/sold-status')
def sold_status():
    csv_path = os.path.join(SAVED_DATA_DIR, 'master_sold.csv')
    ts_path  = os.path.join(SAVED_DATA_DIR, 'master_sold_updated.txt')
    if os.path.exists(csv_path):
        updated = open(ts_path).read().strip() if os.path.exists(ts_path) else 'Unknown'
        return jsonify({'exists': True, 'updated': updated})
    return jsonify({'exists': False})


@app.route('/upload-sold', methods=['POST'])
def upload_sold():
    if 'sold_csv' not in request.files or request.files['sold_csv'].filename == '':
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['sold_csv']
    if not allowed_file(f.filename, ALLOWED_CSV):
        return jsonify({'error': 'Must be a CSV file'}), 400
    os.makedirs(SAVED_DATA_DIR, exist_ok=True)
    f.save(os.path.join(SAVED_DATA_DIR, 'master_sold.csv'))
    timestamp = time.strftime('%B %d, %Y %I:%M %p')
    with open(os.path.join(SAVED_DATA_DIR, 'master_sold_updated.txt'), 'w') as tf:
        tf.write(timestamp)
    return jsonify({'success': True, 'updated': timestamp})


@app.route('/lookup', methods=['POST'])
def lookup():
    from mailer_generator import geocode_address, find_nearest_sold, load_cache
    import pandas as pd

    address = request.form.get('address', '').strip()
    if not address:
        return jsonify({'error': 'Address is required'}), 400

    mapbox_token = request.form.get('mapbox_token') or os.getenv('MAPBOX_TOKEN', '')
    if not mapbox_token:
        return jsonify({'error': 'Mapbox token required'}), 400

    csv_path = os.path.join(SAVED_DATA_DIR, 'master_sold.csv')
    if not os.path.exists(csv_path):
        return jsonify({'error': 'No saved sold list found. Please upload your master sold list first.'}), 400

    num_nearby      = int(request.form.get('num_nearby', 3))
    sqft_pct        = _parse_positive(request.form.get('sqft_pct'))
    age_years       = _parse_positive(request.form.get('age_years'))
    beds_diff       = _parse_positive(request.form.get('beds_diff'))
    lot_size_pct    = _parse_positive(request.form.get('lot_size_pct'))
    match_prop_type = request.form.get('match_prop_type') == 'on'
    filter_settings = {
        'sqft_pct': sqft_pct, 'age_years': age_years, 'beds_diff': beds_diff,
        'lot_size_pct': lot_size_pct, 'match_prop_type': match_prop_type,
    } if any([sqft_pct, age_years, beds_diff, lot_size_pct, match_prop_type]) else None

    client_sqft      = _parse_positive(request.form.get('client_sqft'))
    client_yr        = _parse_positive(request.form.get('client_yr'))
    client_beds      = _parse_positive(request.form.get('client_beds'))
    client_lot       = _parse_positive(request.form.get('client_lot_size'))
    client_prop_type = request.form.get('client_prop_type', '').strip() or None
    client_row = {
        'Sq Ft': client_sqft, 'Yr Built': client_yr, 'Beds': client_beds,
        'Lot Size': client_lot, 'Property Type': client_prop_type,
    } if any([client_sqft, client_yr, client_beds, client_lot, client_prop_type]) else None

    try:
        cache  = load_cache()
        coords = geocode_address({'Address': address, 'City': 'Bakersfield', 'ZIP': ''}, mapbox_token, cache)
        if not coords:
            return jsonify({'error': f'Could not geocode: {address}'}), 400

        df_sold = pd.read_csv(csv_path)
        df_sold = df_sold[df_sold['Address'].str.contains('The information', na=False) == False]
        df_sold['coords'] = [geocode_address(r, mapbox_token, cache) for _, r in df_sold.iterrows()]
        valid_sold = df_sold.dropna(subset=['coords']).copy()

        if valid_sold.empty:
            return jsonify({'error': 'Could not geocode any sold properties. Check your Mapbox token.'}), 400

        results = find_nearest_sold(coords, valid_sold, n=num_nearby, client_row=client_row, filter_settings=filter_settings)

        comparables = [{
            'address':  r.get('Address', ''),
            'price':    int(r.get('Purchase Amt', 0)),
            'beds':     r.get('Beds', ''),
            'baths':    r.get('Baths', ''),
            'sqft':     int(r.get('Sq Ft', 0)) if r.get('Sq Ft') else '',
            'yr_built': int(r.get('Yr Built', 0)) if r.get('Yr Built') else '',
            'distance': round(r.get('distance', 0), 2),
        } for r in results]

        return jsonify({'comparables': comparables})

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# ─── Analyzer Routes ───────────────────────────────────────────────────────────

@app.route('/analyzer')
def analyzer():
    return render_template(
        'analyzer.html',
        default_maps_key=os.getenv('GOOGLE_MAPS_API_KEY', ''),
        default_gemini_key=os.getenv('GEMINI_API_KEY', ''),
    )


def _run_analysis_job(job_id, params):
    job = analyze_jobs[job_id]
    job['status'] = 'running'

    def progress_cb(done, total, address):
        job['done'] = done
        job['total'] = total
        job['current_address'] = address

    try:
        results = run_full_analysis(
            prompts=params['prompts'],
            enabled_prompts=params.get('enabled_prompts'),
            maps_key=params['maps_key'],
            gemini_key=params['gemini_key'],
            progress_cb=progress_cb,
            csv_path=params.get('csv_path'),
            single_address=params.get('single_address'),
            max_addresses=params.get('max_addresses'),
        )
        job['results'] = results
        job['status'] = 'done'
        job['done'] = len(results)
        job['total'] = len(results)
        job['current_address'] = ''
        job['finished_at'] = time.time()
    except Exception as e:
        import traceback
        job['status'] = 'failed'
        job['message'] = str(e)
        job['traceback'] = traceback.format_exc()
        job['finished_at'] = time.time()


@app.route('/analyze', methods=['POST'])
def analyze():
    job_id = str(uuid.uuid4())
    job_dir = tempfile.mkdtemp(prefix='analyzer_job_')
    uploads_dir = os.path.join(job_dir, 'uploads')
    os.makedirs(uploads_dir, exist_ok=True)

    try:
        prompts = [
            request.form.get('prompt_1', '').strip(),
            request.form.get('prompt_2', '').strip(),
            request.form.get('prompt_3', '').strip(),
            request.form.get('prompt_4', '').strip(),
        ]
        enabled_prompts = [
            request.form.get('prompt_1_enabled') == 'on',
            request.form.get('prompt_2_enabled') == 'on',
            request.form.get('prompt_3_enabled') == 'on',
            request.form.get('prompt_4_enabled') == 'on',
        ]
        if not any(enabled_prompts):
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'At least one prompt must be enabled'}), 400

        maps_key = request.form.get('maps_key', '').strip()
        gemini_key = request.form.get('gemini_key', '').strip()
        if not maps_key:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Google Maps API key is required'}), 400
        if not gemini_key:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Gemini API key is required'}), 400

        input_mode = request.form.get('input_mode', 'csv')
        csv_path = None
        single_address = None
        total_addresses = 1

        if input_mode == 'single':
            single_address = request.form.get('single_address', '').strip()
            if not single_address:
                shutil.rmtree(job_dir, ignore_errors=True)
                return jsonify({'error': 'Address is required'}), 400
        else:
            if 'csv_file' not in request.files or request.files['csv_file'].filename == '':
                shutil.rmtree(job_dir, ignore_errors=True)
                return jsonify({'error': 'CSV file is required'}), 400
            f = request.files['csv_file']
            if not allowed_file(f.filename, ALLOWED_CSV):
                shutil.rmtree(job_dir, ignore_errors=True)
                return jsonify({'error': 'File must be a CSV'}), 400
            csv_path = os.path.join(uploads_dir, secure_filename(f.filename))
            f.save(csv_path)

            try:
                df = parse_csv(csv_path)
                full_count = len(df)
            except Exception:
                full_count = 0

            max_raw = request.form.get('max_addresses', '').strip()
            try:
                max_addresses = int(max_raw) if max_raw else None
            except ValueError:
                max_addresses = None
            total_addresses = min(full_count, max_addresses) if max_addresses else full_count

        analyze_jobs[job_id] = {
            'status': 'queued',
            'done': 0,
            'total': total_addresses,
            'current_address': '',
            'message': '',
            'results': None,
            'job_dir': job_dir,
            'finished_at': None,
        }

        params = {
            'prompts': prompts,
            'enabled_prompts': enabled_prompts,
            'maps_key': maps_key,
            'gemini_key': gemini_key,
            'csv_path': csv_path,
            'single_address': single_address,
            'max_addresses': max_addresses if input_mode != 'single' else None,
        }

        thread = threading.Thread(target=_run_analysis_job, args=(job_id, params), daemon=True)
        thread.start()

        return jsonify({'job_id': job_id}), 202

    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500


@app.route('/analyze/status/<job_id>')
def analyze_status(job_id):
    job = analyze_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'status': job['status'],
        'done': job['done'],
        'total': job['total'],
        'current_address': job.get('current_address', ''),
        'message': job.get('message', ''),
    })


@app.route('/analyze/download/<job_id>')
def analyze_download(job_id):
    job = analyze_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if job['status'] != 'done':
        return jsonify({'error': 'Job not finished yet'}), 400
    if not job.get('results'):
        return jsonify({'error': 'No results'}), 404

    results = job['results']
    if not results:
        return jsonify({'error': 'No results'}), 404

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(results[0].keys()))
    writer.writeheader()
    writer.writerows(results)
    csv_bytes = output.getvalue().encode('utf-8')

    # Cleanup after download
    shutil.rmtree(job.get('job_dir', ''), ignore_errors=True)
    analyze_jobs.pop(job_id, None)

    return Response(
        csv_bytes,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=property_analysis.csv'},
    )


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
