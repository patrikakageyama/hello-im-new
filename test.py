#!/usr/bin/env python3
"""
PyPI Top 250 per Trove Classifier — v2
---------------------------------------
Uses PyPI's warehouse XML-RPC API for classifier→package lookup (correct method)
Then pypistats.org for real download counts.
Includes full checkpoint/resume system.
"""

import requests
import pandas as pd
import time
import json
import os
import xmlrpc.client
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
OUTPUT_FILE      = "1_raw_pypi_top250.csv"
CHECKPOINT_FILE  = "checkpoint.json"
TOP_N            = 250
SLEEP_XMLRPC     = 0.3   # between XML-RPC calls
SLEEP_STATS      = 0.5   # between pypistats calls
MAX_RETRIES      = 3
HEADERS          = {"User-Agent": "pypi-research-pipeline/1.0 (academic)"}
PYPI_XMLRPC      = "https://pypi.org/pypi"

# ── Checkpoint helpers ────────────────────────────────────────────────────────
def save_checkpoint(data):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f)

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return None

# ── Step 1: Get all Topic:: classifiers ──────────────────────────────────────
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

# ── Step 2: Get packages per classifier via XML-RPC ──────────────────────────
def get_packages_for_classifier(client, classifier):
    """
    Uses PyPI XML-RPC browse() — the CORRECT method for classifier filtering.
    Returns list of (package_name, version) tuples filtered to unique names.
    """
    for attempt in range(MAX_RETRIES):
        try:
            results = client.browse([classifier])
            # results = [(name, version), ...] — deduplicate by name
            names = list({name for name, version in results})
            return names
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"\n      [xmlrpc error] {e} — retrying in {wait}s...", end="", flush=True)
            time.sleep(wait)
    return []

# ── Step 3: Get download count from pypistats ─────────────────────────────────
def get_downloads(package_name):
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

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    start = datetime.now()

    print("=" * 68)
    print("  PyPI Top 250 per Trove Classifier — v2 (with checkpoint)")
    print("=" * 68)

    # ── Check for existing checkpoint ────────────────────────────────
    checkpoint = load_checkpoint()
    if checkpoint:
        print(f"\n  ♻️  Checkpoint found! Resuming from where we left off...")
        topic_classifiers = checkpoint["topic_classifiers"]
        total_all_clf     = checkpoint["total_all_clf"]
        clf_pkg_map       = checkpoint.get("clf_pkg_map", {})
        dl_cache          = checkpoint.get("dl_cache", {})
        step              = checkpoint.get("step", 1)
        print(f"      Classifiers already fetched: {len(clf_pkg_map)}/{len(topic_classifiers)}")
        print(f"      Download counts cached:      {len(dl_cache)}")
    else:
        clf_pkg_map = {}
        dl_cache    = {}
        step        = 1

        # STEP 1 ─ classifiers
        topic_classifiers, total_all_clf = get_topic_classifiers()
        save_checkpoint({
            "step": 2,
            "topic_classifiers": topic_classifiers,
            "total_all_clf": total_all_clf,
            "clf_pkg_map": {},
            "dl_cache": {}
        })

    total_topic = len(topic_classifiers)

    # ── STEP 2 ─ packages per classifier via XML-RPC ─────────────────
    if step <= 2:
        print(f"\n[2/4] Fetching package lists for {total_topic} classifiers via XML-RPC...")
        print("      (PyPI XML-RPC browse() — correct classifier filter)")

        client = xmlrpc.client.ServerProxy(PYPI_XMLRPC)

        for i, clf in enumerate(topic_classifiers, 1):
            if clf in clf_pkg_map:
                print(f"  [{i:>3}/{total_topic}] SKIP (cached) {clf[:50]}")
                continue

            pkgs = get_packages_for_classifier(client, clf)
            clf_pkg_map[clf] = pkgs
            print(f"  [{i:>3}/{total_topic}] {clf[:58]:<58} → {len(pkgs):>5} pkgs")

            # Save checkpoint every 10 classifiers
            if i % 10 == 0:
                save_checkpoint({
                    "step": 2,
                    "topic_classifiers": topic_classifiers,
                    "total_all_clf": total_all_clf,
                    "clf_pkg_map": clf_pkg_map,
                    "dl_cache": dl_cache
                })

            time.sleep(SLEEP_XMLRPC)

        save_checkpoint({
            "step": 3,
            "topic_classifiers": topic_classifiers,
            "total_all_clf": total_all_clf,
            "clf_pkg_map": clf_pkg_map,
            "dl_cache": dl_cache
        })

    # ── STEP 3 ─ download counts ──────────────────────────────────────
    all_unique = set()
    for pkgs in clf_pkg_map.values():
        all_unique.update(pkgs)
    total_unique = len(all_unique)

    # Only fetch what we don't have cached
    remaining = [p for p in sorted(all_unique) if p not in dl_cache]

    print(f"\n[3/4] Fetching download counts...")
    print(f"      Total unique packages:    {total_unique}")
    print(f"      Already cached:           {total_unique - len(remaining)}")
    print(f"      Still to fetch:           {len(remaining)}")
    print(f"      Source: pypistats.org (last 30 days)")

    for i, pkg in enumerate(remaining, 1):
        dl_cache[pkg] = get_downloads(pkg)

        if i % 100 == 0 or i == len(remaining):
            pct = (i / len(remaining)) * 100
            elapsed_so_far = datetime.now() - start
            print(f"      {i:>6}/{len(remaining)}  ({pct:5.1f}%)   elapsed: {elapsed_so_far}", flush=True)
            # Save checkpoint every 100 packages
            save_checkpoint({
                "step": 3,
                "topic_classifiers": topic_classifiers,
                "total_all_clf": total_all_clf,
                "clf_pkg_map": clf_pkg_map,
                "dl_cache": dl_cache
            })

        time.sleep(SLEEP_STATS)

    # ── STEP 4 ─ rank top 250 per classifier ─────────────────────────
    print(f"\n[4/4] Ranking top {TOP_N} per classifier by download count...")

    all_rows = []
    summary  = []

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

    # Clean up checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

    # ── Final summary ─────────────────────────────────────────────────
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
