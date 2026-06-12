"""
Property Analyzer — image fetch + Gemini analysis logic.

Fetches 3 Street View angles + satellite per address,
sends all to Gemini in one call with 4 custom prompts,
returns a dict with Prompt_1_Response ... Prompt_4_Response.
"""

import base64
import os
import re
import threading
import time
import traceback
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from google import genai
from google.genai import types

GEMINI_MODEL = "gemini-3.5-flash"
GREY_THRESHOLD_BYTES = 8_000

# ── Concurrency config (env-overridable so a paid tier can scale up without code changes) ──
# Free tier defaults: ~10-15 req/min and deprioritized, so keep these low.
# After upgrading to a paid Gemini tier, bump these in Railway env vars, e.g.:
#   ANALYZER_CONCURRENCY=10   ANALYZER_GEMINI_RPM=300
MAX_CONCURRENT = int(os.getenv('ANALYZER_CONCURRENCY', '3'))
GEMINI_RPM = int(os.getenv('ANALYZER_GEMINI_RPM', '12'))


def _log(level, msg):
    import sys
    print(f"[ANALYZER:{level}] {msg}", file=sys.stderr, flush=True)


class _RateLimiter:
    """Thread-safe limiter that spaces out call *starts* to stay under a req/min cap.
    Each acquire() reserves the next evenly-spaced slot, then sleeps until it arrives."""

    def __init__(self, rpm: int):
        self.min_interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self):
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_interval
        wait = slot - time.monotonic()
        if wait > 0:
            time.sleep(wait)

SV_LABELS = [
    "IMAGE 1 — Street View WIDE (FOV 120°): full street context and neighbourhood around the property.",
    "IMAGE 2 — Street View STANDARD (FOV 90°): direct front-facing view of the property exterior.",
    "IMAGE 3 — Street View ZOOMED (FOV 50°, pitched up 10°): close-up detail view of the roof and upper exterior.",
]


def fetch_street_view(address: str, maps_key: str, fov: int = 90, pitch: int = 0):
    params = {
        "size": "640x640",
        "location": address,
        "key": maps_key,
        "fov": fov,
        "pitch": pitch,
    }
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/streetview",
            params=params,
            timeout=15,
        )
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
            if len(r.content) < GREY_THRESHOLD_BYTES:
                _log("INFO", f"street_view grey placeholder fov={fov} addr={address}")
                return None
            return r.content
        else:
            _log("WARN", f"street_view bad status={r.status_code} fov={fov} addr={address}")
    except Exception as e:
        _log("ERROR", f"street_view fetch failed fov={fov} addr={address} error={e}")
    return None


def fetch_satellite(address: str, maps_key: str, zoom: int = 18):
    params = {
        "center": address,
        "zoom": zoom,
        "size": "640x640",
        "maptype": "satellite",
        "markers": f"color:red|{address}",
        "key": maps_key,
    }
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/staticmap",
            params=params,
            timeout=15,
        )
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image"):
            return r.content
        else:
            _log("WARN", f"satellite bad status={r.status_code} addr={address}")
    except Exception as e:
        _log("ERROR", f"satellite fetch failed addr={address} error={e}")
    return None


def _to_part(image_bytes: bytes) -> types.Part:
    return types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")


def _parse_answers(text: str) -> dict:
    answers = {}
    for i in range(1, 5):
        m = re.search(
            rf'ANSWER_{i}:\s*(.+?)(?=\nANSWER_{i+1}:|$)',
            text,
            re.DOTALL | re.IGNORECASE,
        )
        answers[f'Prompt_{i}_Response'] = m.group(1).strip() if m else 'No response'
    return answers


def _gemini_with_retry(client, contents, max_retries: int = 3):
    """Call Gemini, retrying on 429 with the delay from the error response."""
    for attempt in range(max_retries):
        _log("INFO", f"Gemini call model={GEMINI_MODEL} attempt={attempt + 1}/{max_retries}")
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=contents)
            _log("INFO", f"Gemini call SUCCESS response_len={len(response.text)}")
            return response
        except Exception as e:
            err = str(e)
            _log("ERROR", f"Gemini call FAILED attempt={attempt + 1} error={err[:300]}")
            if '429' in err or 'RESOURCE_EXHAUSTED' in err:
                m = re.search(r'retry[^\d]*(\d+(?:\.\d+)?)\s*s', err, re.IGNORECASE)
                delay = float(m.group(1)) + 5 if m else 35
                if attempt < max_retries - 1:
                    _log("WARN", f"Rate limited — waiting {delay}s before retry")
                    time.sleep(delay)
                else:
                    _log("ERROR", "Rate limit hit on final attempt — giving up")
                    raise
            else:
                raise


def analyze_with_gemini(sv_images: list, sat_image, prompts: list, gemini_key: str, enabled_prompts: list = None, rate_limiter=None) -> dict:
    """
    1 Gemini call per address — only enabled prompts sent, disabled ones return 'Prompt not selected'.
    enabled_prompts: list of 4 booleans; defaults to all True if not provided.
    """
    if enabled_prompts is None:
        enabled_prompts = [True] * 4

    prompts = (list(prompts) + [''] * 4)[:4]
    enabled_prompts = (list(enabled_prompts) + [True] * 4)[:4]

    # Build answers dict — pre-fill disabled prompts
    answers = {}
    for i in range(4):
        if not enabled_prompts[i]:
            answers[f'Prompt_{i+1}_Response'] = 'Prompt not selected'

    active = [(i, prompts[i]) for i in range(4) if enabled_prompts[i]]
    if not active:
        return answers

    client = genai.Client(api_key=gemini_key)

    contents = []
    for i, img in enumerate(sv_images[:3]):
        contents.append(SV_LABELS[i])
        contents.append(_to_part(img))
    if sat_image:
        contents.append(
            "IMAGE 4 — Satellite aerial view (zoom 18): top-down view of the property lot, roof, driveway, and surrounding homes."
        )
        contents.append(_to_part(sat_image))

    # Number the active prompts 1..N in the Gemini request
    combined_prompt = (
        "You are a real estate analyst examining this property through street view and satellite images.\n\n"
        "Answer each prompt. For grading prompts, give the grade (e.g. 7/10) then write "
        "3-4 sentences of specific visual evidence that justifies the grade — "
        "name the materials, colors, visible damage, weathering, or standout features you actually see.\n\n"
        "Return ONLY this format:\n\n"
    )
    for seq, (_, p) in enumerate(active, 1):
        combined_prompt += f"ANSWER_{seq}: [grade and detailed visual explanation]\n"
    combined_prompt += "\n"
    for seq, (_, p) in enumerate(active, 1):
        combined_prompt += f"PROMPT {seq}: {p}\n"

    contents.append(combined_prompt)

    _log("INFO", f"Sending {len(active)} prompts + {len([x for x in contents if isinstance(x, types.Part)])} images to Gemini")
    if rate_limiter is not None:
        rate_limiter.acquire()
    response = _gemini_with_retry(client, contents)

    # Parse sequential answers and map back to original prompt positions
    raw = response.text
    for seq, (orig_idx, _) in enumerate(active, 1):
        m = re.search(rf'ANSWER_{seq}:\s*(.+?)(?=\nANSWER_{seq+1}:|$)', raw, re.DOTALL | re.IGNORECASE)
        answers[f'Prompt_{orig_idx+1}_Response'] = m.group(1).strip() if m else 'No response'

    return answers


def build_full_address(row) -> str:
    zip_str = str(row.get('ZIP', '')).split('.')[0].strip()
    return f"{str(row['Address']).strip()}, {str(row['City']).strip()}, {str(row['State']).strip()} {zip_str}"


def parse_csv(csv_path: str):
    import pandas as pd
    df = pd.read_csv(csv_path)
    # Filter PropertyRadar disclaimer footer row
    df = df[~df['Address'].astype(str).str.startswith('The information')]
    df = df.dropna(subset=['Address'])
    df = df[df['Address'].astype(str).str.strip() != '']
    return df


def run_full_analysis(
    prompts: list,
    maps_key: str,
    gemini_key: str,
    progress_cb=None,
    csv_path: str = None,
    single_address: str = None,
    max_addresses: int = None,
    enabled_prompts: list = None,
    return_images: bool = False,
) -> list:
    """
    Main analysis runner. Returns list of row dicts with original columns
    plus Prompt_1_Response ... Prompt_4_Response and Street_View_Available.
    """
    if single_address:
        rows = [{'Address': single_address, 'City': '', 'State': '', 'ZIP': ''}]
        addresses = [single_address]
    else:
        df = parse_csv(csv_path)
        if max_addresses:
            df = df.head(max_addresses)
        rows = df.to_dict('records')
        addresses = [build_full_address(r) for r in rows]

    total = len(addresses)
    angles = [(120, 0), (90, 0), (50, 10)]
    rate_limiter = _RateLimiter(GEMINI_RPM)

    # Process several addresses at once; Gemini calls are spaced by the rate limiter
    # so we never exceed the tier's req/min cap regardless of worker count.
    workers = max(1, min(MAX_CONCURRENT, total))
    _log("INFO", f"Starting analysis: {total} addresses, {workers} concurrent workers, {GEMINI_RPM} RPM cap")

    def _process_one(idx: int, row, address: str) -> dict:
        _log("INFO", f"--- Address {idx + 1}/{total}: {address} ---")

        # Fetch all 4 images in parallel (3 Street View angles + satellite)
        with ThreadPoolExecutor(max_workers=4) as ex:
            sv_futures = [
                ex.submit(fetch_street_view, address, maps_key, fov=fov, pitch=pitch)
                for fov, pitch in angles
            ]
            sat_future = ex.submit(fetch_satellite, address, maps_key)
            sv_images = [img for img in (f.result() for f in sv_futures) if img]
            sat = sat_future.result()

        street_view_available = len(sv_images) > 0
        _log("INFO", f"[{idx + 1}/{total}] Images: {len(sv_images)}/3 SV  Satellite: {'YES' if sat else 'NO'}")

        if not sv_images and not sat:
            _log("WARN", f"No images at all for {address} — skipping Gemini call")
            answers = {f'Prompt_{i}_Response': 'No images available for this address' for i in range(1, 5)}
        else:
            try:
                answers = analyze_with_gemini(sv_images, sat, prompts, gemini_key, enabled_prompts, rate_limiter=rate_limiter)
            except Exception as e:
                _log("ERROR", f"Gemini analysis FAILED for {address}\n{traceback.format_exc()}")
                answers = {f'Prompt_{i}_Response': f'Error: {str(e)}' for i in range(1, 5)}

        result_row = dict(row)
        result_row.update(answers)
        result_row['Street_View_Available'] = 'Yes' if street_view_available else 'No'
        if return_images:
            result_row['_sv_images'] = [base64.b64encode(img).decode() for img in sv_images]
            result_row['_sat_image'] = base64.b64encode(sat).decode() if sat else None
        return result_row

    # Pre-allocate so results stay in the original CSV order regardless of finish order
    results = [None] * total
    done_count = 0
    done_lock = threading.Lock()

    if progress_cb:
        progress_cb(0, total, addresses[0] if addresses else '')

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(_process_one, idx, row, address): idx
            for idx, (row, address) in enumerate(zip(rows, addresses))
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                _log("ERROR", f"Address {idx + 1} crashed\n{traceback.format_exc()}")
                row = rows[idx]
                err_row = dict(row)
                err_row.update({f'Prompt_{i}_Response': f'Error: {str(e)}' for i in range(1, 5)})
                err_row['Street_View_Available'] = 'No'
                results[idx] = err_row
            with done_lock:
                done_count += 1
                done_now = done_count
            if progress_cb:
                progress_cb(done_now, total, addresses[idx])

    if progress_cb:
        progress_cb(total, total, 'Done')

    return results
