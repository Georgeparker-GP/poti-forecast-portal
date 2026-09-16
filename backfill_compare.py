#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_compare.py — `vs_portal`-ის ხელახალი გამოთვლა git-ის ისტორიიდან.

═══ რატომ ═══
`data.json` მხოლოდ 48 საათს ინახავს. როცა 2026-09-16-ზე ორი კვირის
დაგროვილი ბიულეტენი ერთად დამუშავდა, `_portal_at()`-მა ისტორიული
საათები ვერ იპოვა და `current`-ს დაუბრუნდა — ანუ 03.09-ის MTA-ს
გაზომვა შეედარა 16.09-ის პორტალს. სკრიპტმა ეს სწორად მონიშნა
`portal_hour_exact: false`-ით, ანუ კალიბრაციაში არ ითვლება.

მაგრამ პორტალის ისტორია ᲐᲠ ᲓᲐᲘᲙᲐᲠᲒᲐ: `data.json` ყოველ საათს
git-ში ჩადის. ეს სკრიპტი commit-ებიდან აღადგენს სწორ საათს და
შედარებებს თავიდან ითვლის.

═══ რას აკეთებს ═══
  1. git-ის ისტორიიდან აგებს {საათი → current-ბლოკი} ინდექსს
  2. ჟურნალის ყველა `exact: false` ჩანაწერს ხელახლა ითვლის
  3. წარმატებისას `portal_hour_exact: true` და `backfilled: true`
  4. ᲬᲘᲜᲐ ᲕᲔᲠᲡᲘᲐᲡ ᲘᲜᲐᲮᲐᲕᲡ — `mta_log.json.bak`

⚠ ᲐᲠᲐᲤᲔᲠᲡ ᲨᲚᲘᲡ. ვერ იპოვა — ჩანაწერი უცვლელი რჩება.
⚠ `THRESHOLDS`, `BASE_WEIGHTS` და კონსენსუსი არ ეხება.

გაშვება (რეპოს ძირში, სრული ისტორიით):
    git fetch --unshallow 2>/dev/null || true
    python3 backfill_compare.py            # ჯერ მშრალი გაშვება
    python3 backfill_compare.py --write    # ჩაწერა
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import mta_ingest as MI

DATA = "data.json"
LOG_FILE = pathlib.Path("mta_log.json")


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def portal_index() -> dict:
    """{'YYYY-MM-DDTHH:00': current-ბლოკი} — git-ის ყველა commit-იდან.

    ვიღებთ `current`-ს: ის იმ საათის nowcast-ია და არა პროგნოზი,
    ანუ ყველაზე ახლოსაა რეალობასთან. forecast-ის ბლოკებიც ვამატებთ
    იმ საათებზე, სადაც current არ მოგვეპოვება.
    """
    idx, cur_hits = {}, 0
    shas = [x.strip() for x in sh("git", "log", "--format=%H", "--", DATA).splitlines() if x.strip()]
    print(f"commit-ები {DATA}-ზე: {len(shas)}")
    for n, sha in enumerate(shas, 1):
        raw = sh("git", "show", f"{sha}:{DATA}")
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        cur = d.get("current") or {}
        t = (cur.get("time") or "")[:16]
        if t and t not in idx:
            idx[t] = cur
            cur_hits += 1
        if n % 250 == 0:
            print(f"  …{n}/{len(shas)}  საათი: {len(idx)}")
    print(f"ინდექსი მზადაა: {len(idx)} საათი (current-იდან {cur_hits})")
    return idx


def target_hour(entry: dict):
    """ჩანაწერის საათი — იგივე ლოგიკა, რაც `_portal_at`-ში."""
    date_s = (entry.get("date") or "").strip()
    time_s = (entry.get("time") or "").strip()
    try:
        parts = [x.strip() for x in date_s.split("/")]
        if len(parts) != 3:
            return None
        day = int(parts[0])
        mon = MI.GEO_MONTHS.get(parts[1])
        if mon is None and parts[1].isdigit():
            mon = int(parts[1])
        year = int(parts[2].replace("წ.", "").strip())
        hh = int(time_s.split(":")[0])
        if mon and 1 <= mon <= 12:
            return f"{year:04d}-{mon:02d}-{day:02d}T{hh:02d}:00"
    except Exception:
        pass
    return None


def main():
    write = "--write" in sys.argv

    if not LOG_FILE.exists():
        sys.exit("mta_log.json ვერ მოიძებნა")
    data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
    entries = data.get("entries", [])

    todo = [e for e in entries if e.get("portal_hour_exact") is not True]
    print(f"ჩანაწერი სულ: {len(entries)}")
    print(f"გადასათვლელი (exact != true): {len(todo)}\n")
    if not todo:
        print("ყველაფერი უკვე ზუსტია.")
        return

    idx = portal_index()
    print()

    fixed = no_hour = no_portal = failed = 0
    for e in todo:
        t = target_hour(e)
        if not t:
            no_hour += 1
            continue
        portal = idx.get(t)
        if not portal:
            no_portal += 1
            continue
        try:
            cmp = MI._compare_to_portal(e, portal)
        except Exception as exc:
            print(f"  ✗ {e.get('bulletin_no')} — {exc}")
            failed += 1
            continue
        if not cmp:
            no_portal += 1
            continue
        e["vs_portal"] = cmp
        e["portal_time"] = t
        e["portal_hour_exact"] = True
        e["backfilled"] = True          # ოქტომბერში გარჩევადი იყოს
        fixed += 1

    print(f"\n═══ შედეგი ═══")
    print(f"  გასწორდა:              {fixed}")
    print(f"  საათი ვერ დადგინდა:    {no_hour}")
    print(f"  პორტალი ისტორიაში არაა:{no_portal}")
    print(f"  შედარება ჩავარდა:      {failed}")

    now_exact = sum(1 for e in entries if e.get("portal_hour_exact") is True)
    print(f"\n  ზუსტი შედარება: {now_exact - fixed} → {now_exact}")

    if not write:
        print("\n(მშრალი გაშვება — ჩასაწერად: --write)")
        return

    shutil.copy2(LOG_FILE, LOG_FILE.with_suffix(".json.bak"))
    LOG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✅ ჩაიწერა. სარეზერვო: {LOG_FILE.with_suffix('.json.bak')}")


if __name__ == "__main__":
    main()
