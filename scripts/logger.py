# scripts/logger.py — IBD Daily Market Report
#
# Same active/archive/re-age mechanic as the other loggers.
# Receives RAW NARRATIVE TEXT and parses it via parser.py.

import os
import sys
import json
from datetime import datetime, timedelta, timezone

from parser import parse_stories

DATA_DIR = "data"
ACTIVE_FILE = os.path.join(DATA_DIR, "active_week.json")
ARCHIVE_FILE = os.path.join(DATA_DIR, "archive.json")

BUCKETS = ["past_2_weeks", "past_1_month", "past_3_months", "past_6_months", "past_1_year", "historical"]


def load_data(path, default_type=list):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default_type()
    return default_type()


def save_data(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def get_raw_texts():
    if os.environ.get("REBUCKET_ONLY") == "1":
        return []

    p_str = os.environ.get("DISPATCH_CLIENT_PAYLOAD")
    if p_str and p_str.strip():
        try:
            p = json.loads(p_str)
        except Exception:
            p = None
        if isinstance(p, dict):
            if p.get("raw_text"):
                return [p["raw_text"]]
            if p.get("raw_text_list"):
                return list(p["raw_text_list"])

    manual = os.environ.get("MANUAL_RAW_TEXT")
    if manual and manual.strip():
        return [manual]

    return []


def get_events():
    events = []
    had_errors = False
    for text in get_raw_texts():
        parsed, errors = parse_stories(text)
        events.extend(parsed)
        for snippet, e in errors:
            had_errors = True
            print(f"::warning::Failed to parse a story (starts: \"{snippet}\"): {e}", file=sys.stderr)
    return events, had_errors


def bucket_for_age(age: timedelta):
    if age <= timedelta(days=7):
        return None
    if age <= timedelta(days=14):
        return "past_2_weeks"
    if age <= timedelta(days=30):
        return "past_1_month"
    if age <= timedelta(days=90):
        return "past_3_months"
    if age <= timedelta(days=180):
        return "past_6_months"
    if age <= timedelta(days=365):
        return "past_1_year"
    return "historical"


def main():
    new_events, had_errors = get_events()
    now = datetime.now(timezone.utc)

    active_items = load_data(ACTIVE_FILE, list)
    archive_dict = load_data(ARCHIVE_FILE, dict)
    if not isinstance(archive_dict, dict):
        archive_dict = {}
    for b in BUCKETS:
        if b not in archive_dict:
            archive_dict[b] = []

    pool = list(new_events)
    pool.extend(active_items)
    for b in BUCKETS:
        pool.extend(archive_dict[b])

    seen = set()
    unique_all = []
    for item in pool:
        uid = f"{item.get('header')}_{item.get('timestamp')}"
        if uid not in seen:
            seen.add(uid)
            unique_all.append(item)

    new_active = []
    new_archive = {b: [] for b in BUCKETS}

    for item in unique_all:
        try:
            dt = datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
        except Exception:
            dt = now
        age = now - dt
        b = bucket_for_age(age)
        if b is None:
            new_active.append(item)
        else:
            new_archive[b].append(item)

    t_sort = lambda x: x.get('timestamp', '')
    new_active.sort(key=t_sort, reverse=True)
    for b in BUCKETS:
        new_archive[b].sort(key=t_sort, reverse=True)

    save_data(ACTIVE_FILE, new_active)
    save_data(ARCHIVE_FILE, new_archive)

    if had_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
