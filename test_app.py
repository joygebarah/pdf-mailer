"""
Integration tests for joy-mailer-webapp.
Runs against a live Flask server on http://localhost:5001
Usage: python test_app.py
"""

import os
import sys
import time
import zipfile
import tempfile
import requests

BASE = "http://localhost:5001"
ROOT = os.path.dirname(os.path.abspath(__file__))
CLIENT_CSV = os.path.join(ROOT, "Clientlist1-25.csv")
SOLD_CSV   = os.path.join(ROOT, "Justsoldtest2-5.csv")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"    [{status}] {name}" + (f"  ({detail})" if detail else ""))
    results.append(condition)

def wait_for_server(timeout=15):
    for _ in range(timeout * 2):
        try:
            requests.get(BASE, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False

# ── helpers ──────────────────────────────────────────────────────────────────

def upload_sold():
    with open(SOLD_CSV, "rb") as f:
        return requests.post(f"{BASE}/upload-sold",
                             files={"sold_csv": ("sold.csv", f, "text/csv")})

def submit_generate(num_clients=2, sqft_pct=None, age_years=None, beds_diff=None, use_saved=False):
    files = {"client_csv": ("clients.csv", open(CLIENT_CSV, "rb"), "text/csv")}
    if not use_saved:
        files["sold_csv"] = ("sold.csv", open(SOLD_CSV, "rb"), "text/csv")
    data = {"num_clients": str(num_clients), "num_nearby": "3"}
    if use_saved:  data["use_saved_sold"] = "on"
    if sqft_pct:   data["sqft_pct"]  = str(sqft_pct)
    if age_years:  data["age_years"] = str(age_years)
    if beds_diff:  data["beds_diff"] = str(beds_diff)
    return requests.post(f"{BASE}/generate", files=files, data=data)

def poll_job(job_id, timeout=120):
    for _ in range(timeout * 2):
        d = requests.get(f"{BASE}/status/{job_id}").json()
        if d["status"] in ("done", "failed"):
            return d
        time.sleep(0.5)
    return {"status": "timeout"}

# ── tests ─────────────────────────────────────────────────────────────────────

def test_home():
    print("\n[1] Home page")
    r = requests.get(BASE)
    check("Returns 200",         r.status_code == 200)
    check("Contains 'Mailer'",   "Mailer" in r.text)

def test_sold_status_empty():
    print("\n[2] /sold-status — before any upload")
    r = requests.get(f"{BASE}/sold-status")
    check("Returns 200",       r.status_code == 200)
    check("'exists' key present", "exists" in r.json())

def test_upload_sold():
    print("\n[3] Upload master sold CSV")
    r = upload_sold()
    check("Returns 200",            r.status_code == 200)
    d = r.json()
    check("success=true",           d.get("success") is True)
    check("updated timestamp set",  bool(d.get("updated")))

def test_sold_status_after_upload():
    print("\n[4] /sold-status — after upload")
    d = requests.get(f"{BASE}/sold-status").json()
    check("exists=true",           d.get("exists") is True)
    check("updated string present", bool(d.get("updated")))

def test_lookup_basic():
    print("\n[5] Quick lookup — no filters")
    r = requests.post(f"{BASE}/lookup", data={"address": "5601 Meany Ave", "num_nearby": "3"})
    check("Returns 200", r.status_code == 200, r.text[:120] if r.status_code != 200 else "")
    d = r.json()
    comps = d.get("comparables", [])
    check("3 comparables returned",   len(comps) == 3,       f"got {len(comps)}")
    check("Each has address",         all(c.get("address")   for c in comps))
    check("Each has price",           all(c.get("price") is not None for c in comps))
    check("Each has distance",        all(c.get("distance") is not None for c in comps))

def test_lookup_with_filters():
    print("\n[6] Quick lookup — sqft + age + beds filters")
    r = requests.post(f"{BASE}/lookup", data={
        "address":     "5601 Meany Ave",
        "num_nearby":  "3",
        "client_sqft": "1500",
        "client_yr":   "1970",
        "client_beds": "3",
        "sqft_pct":    "25",
        "age_years":   "20",
        "beds_diff":   "1",
    })
    check("Returns 200",               r.status_code == 200)
    d = r.json()
    check("No error key",              "error" not in d, d.get("error", ""))
    comps = d.get("comparables", [])
    check("Returns results",           len(comps) > 0,  f"got {len(comps)}")

def test_lookup_lot_size_filter_skipped():
    print("\n[7] Quick lookup — lot size filter (no column in CSV, should be skipped gracefully)")
    r = requests.post(f"{BASE}/lookup", data={
        "address":          "5601 Meany Ave",
        "num_nearby":       "3",
        "client_lot_size":  "6000",
        "lot_size_pct":     "30",
    })
    check("Returns 200 (filter skipped, not crash)", r.status_code == 200)
    d = r.json()
    check("No error",  "error" not in d, d.get("error", ""))
    check("Has results", len(d.get("comparables", [])) > 0)

def test_lookup_bad_address():
    print("\n[8] Quick lookup — empty address (should return 400)")
    r = requests.post(f"{BASE}/lookup", data={"address": "", "num_nearby": "3"})
    check("Returns 400",   r.status_code == 400, f"got {r.status_code}")
    d = r.json()
    check("Returns error", "error" in d, str(d)[:80])

def test_generate_basic():
    print("\n[9] Batch generate — 2 clients, no filters")
    r = submit_generate(num_clients=2)
    check("Returns 202", r.status_code == 202, r.text[:120] if r.status_code != 202 else "")
    d = r.json()
    check("job_id present", "job_id" in d)
    return d.get("job_id")

def test_job_completes(job_id):
    print("\n[10] Job polling — waits until done")
    if not job_id:
        check("Skipped — no job_id", False); return None
    result = poll_job(job_id)
    check("Status = done",       result["status"] == "done",  f"status={result['status']} msg={result.get('message','')}")
    check("pdf_count >= 2",      result.get("pdf_count", 0) >= 2, f"got {result.get('pdf_count')}")
    return job_id

def test_download_zip(job_id):
    print("\n[11] Download ZIP and inspect contents")
    if not job_id:
        check("Skipped — no job_id", False); return
    r = requests.get(f"{BASE}/download/{job_id}")
    check("Returns 200",             r.status_code == 200)
    check("Content-Type is zip",     "zip" in r.headers.get("Content-Type", ""))
    check("File > 100 KB",           len(r.content) > 100_000, f"{len(r.content)//1024} KB")
    tmp = tempfile.mktemp(suffix=".zip")
    with open(tmp, "wb") as f:
        f.write(r.content)
    with zipfile.ZipFile(tmp) as z:
        names = z.namelist()
    check("individual/ PDFs exist",  any("individual/" in n for n in names), str(names))
    check("Merged PDF exists",       any("final_"      in n for n in names), str(names))
    os.unlink(tmp)

def test_generate_with_filters():
    print("\n[12] Batch generate — 2 clients, sqft + age filters")
    r = submit_generate(num_clients=2, sqft_pct=25, age_years=20)
    check("Returns 202", r.status_code == 202)
    d = r.json()
    if "job_id" not in d:
        check("job_id present", False); return
    result = poll_job(d["job_id"])
    check("Finishes (not timeout)",  result["status"] in ("done", "failed"), result["status"])
    check("At least 1 PDF made",     result.get("pdf_count", 0) >= 1,        f"pdf_count={result.get('pdf_count')}")

def test_generate_missing_csv():
    print("\n[13] Validation — missing client CSV")
    r = requests.post(f"{BASE}/generate", data={"num_clients": "1"})
    check("Returns 400",          r.status_code == 400)
    check("Error message in JSON", "error" in r.json())

def test_sold_status_wrong_method():
    print("\n[14] /sold-status rejects POST")
    r = requests.post(f"{BASE}/sold-status")
    check("Returns 405", r.status_code == 405)

# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  joy-mailer-webapp  —  integration tests")
    print("=" * 55)
    print(f"\nWaiting for server at {BASE} ...")
    if not wait_for_server():
        print("  ERROR: server not reachable. Start it with:\n    python app.py")
        sys.exit(1)
    print("  Server ready.\n")

    test_home()
    test_sold_status_empty()
    test_upload_sold()
    test_sold_status_after_upload()
    test_lookup_basic()
    test_lookup_with_filters()
    test_lookup_lot_size_filter_skipped()
    test_lookup_bad_address()
    job_id = test_generate_basic()
    job_id = test_job_completes(job_id)
    test_download_zip(job_id)
    test_generate_with_filters()
    test_generate_missing_csv()
    test_sold_status_wrong_method()

    passed = sum(results)
    total  = len(results)
    print(f"\n{'='*55}")
    print(f"  {passed}/{total} checks passed")
    if passed == total:
        print("  \033[92mAll tests passed!\033[0m")
    else:
        print("  \033[91mSome checks failed — see above.\033[0m")
    print("=" * 55)
    sys.exit(0 if passed == total else 1)
