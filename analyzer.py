"""
Property Analyzer — image fetch + Gemini analysis logic.

Fetches 3 Street View angles + satellite per address,
sends all to Gemini in one call with 4 custom prompts,
returns a dict with Prompt_1_Response ... Prompt_4_Response.
"""

import re
import time
import logging
import requests
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-1.5-flash"
GREY_THRESHOLD_BYTES = 8_000

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
                logger.info("street_view_grey_placeholder", extra={"address": address, "fov": fov})
                return None
            return r.content
    except Exception as e:
        logger.warning("street_view_fetch_error", extra={"address": address, "error": str(e)})
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
    except Exception as e:
        logger.warning("satellite_fetch_error", extra={"address": address, "error": str(e)})
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
        try:
            return client.models.generate_content(model=GEMINI_MODEL, contents=contents)
        except Exception as e:
            err = str(e)
            if '429' in err or 'RESOURCE_EXHAUSTED' in err:
                m = re.search(r'retry[^\d]*(\d+(?:\.\d+)?)\s*s', err, re.IGNORECASE)
                delay = float(m.group(1)) + 5 if m else 35
                if attempt < max_retries - 1:
                    logger.warning("gemini_rate_limited", extra={"attempt": attempt + 1, "wait_s": delay})
                    time.sleep(delay)
                else:
                    raise
            else:
                raise


def analyze_with_gemini(sv_images: list, sat_image, prompts: list, gemini_key: str, enabled_prompts: list = None) -> dict:
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
        "You are analyzing a residential property using the provided street view and satellite images.\n"
        "Answer each of the following prompts separately and concisely.\n"
        "Return ONLY in this exact format:\n\n"
    )
    for seq, (_, p) in enumerate(active, 1):
        combined_prompt += f"ANSWER_{seq}: [your answer]\n"
    combined_prompt += "\n"
    for seq, (_, p) in enumerate(active, 1):
        combined_prompt += f"PROMPT {seq}: {p}\n"

    contents.append(combined_prompt)

    logger.info("gemini_call_start", extra={"active_prompts": len(active), "images": len([x for x in contents if isinstance(x, types.Part)])})
    response = _gemini_with_retry(client, contents)
    logger.info("gemini_call_done", extra={"response_len": len(response.text)})

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
    results = []
    angles = [(120, 0), (90, 0), (50, 10)]

    for idx, (row, address) in enumerate(zip(rows, addresses)):
        if progress_cb:
            progress_cb(idx, total, address)

        logger.info("analyzing_address", extra={"idx": idx + 1, "total": total, "address": address})

        sv_images = []
        for fov, pitch in angles:
            img = fetch_street_view(address, maps_key, fov=fov, pitch=pitch)
            if img:
                sv_images.append(img)

        sat = fetch_satellite(address, maps_key)
        street_view_available = len(sv_images) > 0

        if not sv_images and not sat:
            logger.warning("no_images_for_address", extra={"address": address})
            answers = {f'Prompt_{i}_Response': 'No images available for this address' for i in range(1, 5)}
        else:
            try:
                answers = analyze_with_gemini(sv_images, sat, prompts, gemini_key, enabled_prompts)
            except Exception as e:
                logger.error("gemini_analysis_error", extra={"address": address, "error": str(e)})
                answers = {f'Prompt_{i}_Response': f'Error: {str(e)}' for i in range(1, 5)}

        result_row = dict(row)
        result_row.update(answers)
        result_row['Street_View_Available'] = 'Yes' if street_view_available else 'No'
        results.append(result_row)

        # Free tier: 10 RPM → 6s sleep between addresses
        if idx < total - 1:
            time.sleep(6)

    if progress_cb:
        progress_cb(total, total, 'Done')

    return results
