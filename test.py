#!/usr/bin/env python3
"""
PyPI Top 250 per Trove Classifier
----------------------------------
Step 1 : Fetch all Topic:: classifiers from PyPI
Step 2 : For each classifier, get all packages listed under it (PyPI Simple API)
Step 3 : Fetch real download counts from pypistats.org (last 30 days, free)
Step 4 : Rank and keep top 250 per classifier by download count
Step 5 : Save 1_raw_pypi_top250.csv and print all numbers
"""

import requests
import pandas as pd
import time
import sys
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_FILE   = "1_raw_pypi_top250.csv"
TOP_N         = 250
SLEEP_API     = 0.5    # seconds between pypistats calls
SLEEP_SIMPLE  = 0.2    # seconds between PyPI simple calls
MAX_RETRIES   = 3
HEADERS       = {"User-Agent": "pypi-research-pipeline/1.0 (academic)"}

# ── Step 1: Get all Topic:: classifiers ───────────────────────────────────────
def get_topic_classifiers():
    print("\n[1/4] Fetching all Trove classifiers from PyPI...")
    url = "https://pypi.org/pypi?%3Aaction=list_classifiers"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    all_clf   = [l.strip() for l in r.text.strip().splitlines() if l.strip()]
    topic_clf = [c for c in all_clf if c.startswith("Topic :: ")]
    print(f"      All Trove classifiers:    {len(all_clf)}")
    print(f"      Topic :: classifiers:     {len(topic_clf)}")
    return topic_clf, len(all_clf)

# ── Step 2: Get packages for one classifier ────────────────────────────────────
def get_packages_for_classifier(classifier):
    """Uses PyPI Simple API with classifier filter (PEP 691 / PEP 714)."""
    url     = "https://pypi.org/simple/"
    headers = {**HEADERS, "Accept": "application/vnd.pypi.simple.v1+json"}
    params  = {"classifier": classifier}
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=60)
            if r.status_code == 200:
                data = r.json()
                return [p["name"] for p in data.get("projects", [])]
            elif r.status_code == 429:
                time.sleep(60)
            else:
                return []
        except Exception:
            time.sleep(5 * (attempt + 1))
    return []

# ── Step 3: Get download count from pypistats ──────────────────────────────────
def get_downloads(package_name):
    """Returns last-month download count. Free, no key needed."""
    url = f"https://pypistats.org/api/packages/{package_name.lower()}/recent"
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json().get("data", {}).get("last_month", 0)
            elif r.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"\n      [rate limit] sleeping {wait}s...", end="", flush=True)
                time.sleep(wait)
            else:
                return 0
        except Exception:
            time.sleep(3 * (attempt + 1))
    return 0

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    start = datetime.now()

    print("=" * 68)
    print("  PyPI Top 250 per Trove Classifier — full pipeline")
    print("=" * 68)

    # STEP 1 ─ classifiers
    topic_classifiers, total_all_clf = get_topic_classifiers()
    total_topic = len(topic_classifiers)

    # STEP 2 ─ packages per classifier
    print(f"\n[2/4] Fetching package lists for {total_topic} Topic:: classifiers...")
    clf_pkg_map = {}   # classifier → [pkg_name, ...]

    for i, clf in enumerate(topic_classifiers, 1):
        pkgs = get_packages_for_classifier(clf)
        clf_pkg_map[clf] = pkgs
        short = clf[:58]
        print(f"  [{i:>3}/{total_topic}] {short:<58} → {len(pkgs):>5} pkgs")
        time.sleep(SLEEP_SIMPLE)

    # STEP 3 ─ unique packages → download counts
    all_unique = set()
    for pkgs in clf_pkg_map.values():
        all_unique.update(pkgs)
    total_unique = len(all_unique)

    print(f"\n[3/4] Fetching download counts for {total_unique} unique packages...")
    print("      Source: pypistats.org (last 30 days, free)")

    dl_cache = {}
    pkg_list = sorted(all_unique)

    for i, pkg in enumerate(pkg_list, 1):
        dl_cache[pkg] = get_downloads(pkg)
        if i % 100 == 0 or i == total_unique:
            pct = (i / total_unique) * 100
            elapsed_so_far = datetime.now() - start
            print(f"      {i:>6}/{total_unique}  ({pct:5.1f}%)   elapsed: {elapsed_so_far}", flush=True)
        time.sleep(SLEEP_API)

    # STEP 4 ─ rank top 250 per classifier
    print(f"\n[4/4] Ranking top {TOP_N} per classifier by download count...")

    all_rows    = []
    summary     = []

    for clf, pkgs in clf_pkg_map.items():
        if not pkgs:
            summary.append((clf, 0, 0))
            continue
        ranked = sorted(pkgs, key=lambda p: dl_cache.get(p, 0), reverse=True)
        top    = ranked[:TOP_N]
        for rank, pkg in enumerate(top, 1):
            all_rows.append({
                "classifier":      clf,
                "package_name":    pkg,
                "total_downloads": dl_cache.get(pkg, 0),
                "rank":            rank
            })
        summary.append((clf, len(pkgs), len(top)))

    # Save CSV
    df = pd.DataFrame(all_rows, columns=["classifier","package_name","total_downloads","rank"])
    df.to_csv(OUTPUT_FILE, index=False)

    # ── Print summary table ────────────────────────────────────────────
    elapsed = datetime.now() - start
    print("\n" + "=" * 68)
    print("  CLASSIFIER SUMMARY")
    print("=" * 68)
    print(f"  {'Classifier':<58} | {'Found':>6} | {'Taken':>5}")
    print("-" * 68)
    for clf, found, taken in summary:
        print(f"  {clf[:57]:<57} | {found:>6} | {taken:>5}")

    print("\n" + "=" * 68)
    print("  FINAL NUMBERS")
    print("=" * 68)
    print(f"  All Trove classifiers (total):      {total_all_clf}")
    print(f"  Topic:: classifiers only:           {total_topic}")
    print(f"  Total unique packages queried:      {total_unique}")
    print(f"  Total rows written to CSV:          {len(all_rows)}")
    print(f"  Max possible rows (250×classifiers):{total_topic * TOP_N}")
    print(f"  Output saved to:                    {OUTPUT_FILE}")
    print(f"  Total time elapsed:                 {elapsed}")
    print("=" * 68)
    print(f"\n  ✅  Done!  →  {OUTPUT_FILE}  ({len(all_rows)} rows)")

if __name__ == "__main__":
    main()
