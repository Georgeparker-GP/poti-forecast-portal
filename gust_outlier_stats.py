#!/usr/bin/env python3
"""ქროლვის მაქსიმუმი — რამდენად სცილდება დანარჩენ მოდელებს.

კითხვა: `wind_gusts = max(_gusts_real)` ხომ არ ქმნის ცრუ განგაშის რისკს,
როცა ერთი მოდელი "აურევს"? სანამ ფილტრს დავამატებთ, გავზომოთ,
ეს რისკი რამდენჯერ განხორციელდა ᲠᲔᲐᲚᲣᲠᲐᲓ.

წყარო: data.json-ის `models_now` git-ის ისტორიიდან.

⚠ ᲛᲮᲝᲚᲝᲓ ᲐᲜᲐᲚᲘᲖᲘᲐ. არაფერს არ ცვლის.

⚠ ᲛᲜᲘᲨᲕᲜᲔᲚᲝᲕᲐᲜᲘ: Open-Meteo-ს best_match ფოთზე ხშირად ICON-EU-ს
   ირჩევს და ორივე იდენტურ მნიშვნელობას აბრუნებს. ასეთი წყვილი
   ᲔᲠᲗᲐᲓ ითვლება — თორემ "ორი მოდელი ეთანხმება" ილუზია გამოვა.

გაშვება (რეპოს ძირში, სრული ისტორიით):
    python3 gust_outlier_stats.py
"""

import json
import subprocess
import sys
from collections import Counter

FILE = "data.json"
MODELS = ("best", "gfs", "icon_eu", "ecmwf", "yr_no")

# რამდენით უნდა აღემატებოდეს მაქსიმუმი სიდიდით მეორეს, რომ
# "საეჭვო გამონაკლისად" ჩაითვალოს
GAPS = (1.0, 2.0, 3.0, 5.0)

# ზღვრები fetch.py-დან — რომელი გაფრთხილება იდგებოდა საფრთხის ქვეშ
THR = {"barge": 10.0, "vessel": 17.0, "suspended": 21.5}


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def snapshots():
    for line in sh("git", "log", "--format=%H", "--", FILE).splitlines():
        sha = line.strip()
        if not sha:
            continue
        raw = sh("git", "show", f"{sha}:{FILE}")
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        mn = d.get("models_now")
        cur = d.get("current") or {}
        if mn:
            yield cur.get("time"), mn, cur


def gusts(mn):
    """{მოდელი: ქროლვა}. იდენტური მნიშვნელობები ერთ ჯგუფად."""
    out = {}
    for m in MODELS:
        v = (mn.get(m) or {}).get("g")
        if v is not None:
            try:
                out[m] = float(v)
            except (TypeError, ValueError):
                pass
    return out


def main():
    rows, dup_pairs = [], Counter()
    seen = set()

    for t, mn, cur in snapshots():
        g = gusts(mn)
        if len(g) < 3:
            continue
        key = t or len(seen)
        if key in seen:
            continue
        seen.add(key)

        # იდენტური წყვილების აღრიცხვა (best ≡ icon_eu და მისთ.)
        names = list(g)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if abs(g[names[i]] - g[names[j]]) < 1e-9:
                    dup_pairs[f"{names[i]}≡{names[j]}"] += 1

        # უნიკალური მნიშვნელობები — დუბლიკატი ერთხელ ითვლება
        uniq = sorted(set(g.values()), reverse=True)
        if len(uniq) < 2:
            continue
        top, second = uniq[0], uniq[1]
        owners = [m for m, v in g.items() if v == top]
        rows.append({
            "t": t, "top": top, "second": second, "gap": top - second,
            "owners": owners, "n_uniq": len(uniq),
            "portal": (cur or {}).get("wind_gusts"),
        })

    if not rows:
        sys.exit("models_now-ის ჩანაწერი ვერ მოიძებნა.")

    rows.sort(key=lambda r: r["t"] or "")
    print(f"საათი models_now-ით: {len(rows)}")
    print(f"პერიოდი: {rows[0]['t']}  →  {rows[-1]['t']}\n")

    print("═══ იდენტური მოდელები (დამოუკიდებლობის შემოწმება) ═══")
    for pair, n in dup_pairs.most_common():
        print(f"  {pair:24s} {n:5d} საათი  ({n/len(rows)*100:.1f}%)")
    if not dup_pairs:
        print("  იდენტური წყვილი არ არის.")

    print("\n═══ მაქსიმუმის განცალკევება ═══")
    for gap in GAPS:
        hits = [r for r in rows if r["gap"] >= gap]
        print(f"  მაქსიმუმი მეორეზე ≥{gap:>4.1f} მ/წმ მეტი: "
              f"{len(hits):5d}  ({len(hits)/len(rows)*100:5.2f}%)")

    print("\n═══ ᲠᲘᲡᲙᲘ: ᲒᲐᲤᲠᲗᲮᲘᲚᲔᲑᲐ ᲛᲮᲝᲚᲝᲓ ᲛᲐᲥᲡᲘᲛᲣᲛᲖᲔ ᲓᲒᲐᲡ ═══")
    print("(მაქსიმუმი ზღვარს კვეთს, სიდიდით მეორე — არა)")
    any_risk = False
    for name, lim in THR.items():
        hits = [r for r in rows if r["top"] >= lim > r["second"]]
        print(f"\n  {name} (≥{lim}): {len(hits)} საათი")
        if hits:
            any_risk = True
            for r in hits[:12]:
                print(f"    {r['t']}  მაქს {r['top']:5.1f} "
                      f"({','.join(r['owners'])})  მეორე {r['second']:5.1f}  "
                      f"სხვაობა {r['gap']:.1f}")
            if len(hits) > 12:
                print(f"    ... და კიდევ {len(hits)-12}")
    if not any_risk:
        print("\n  ᲐᲠᲪᲔᲠᲗᲘ ᲨᲔᲛᲗᲮᲕᲔᲕᲐ. ცრუ განგაშის რისკი ამ მონაცემზე")
        print("  არ განხორციელებულა — max-ის შეცვლა გამართლებული არ არის.")

    print("\n═══ ვინ იძლევა მაქსიმუმს ═══")
    c = Counter(m for r in rows for m in r["owners"])
    for m, n in c.most_common():
        print(f"  {m:10s} {n:5d}  ({n/len(rows)*100:5.1f}%)")

    print("\n═══ ყველაზე დიდი განცალკევება — 15 საათი ═══")
    for r in sorted(rows, key=lambda x: -x["gap"])[:15]:
        print(f"  სხვაობა {r['gap']:5.1f}   მაქს {r['top']:5.1f} "
              f"({','.join(r['owners'])})  მეორე {r['second']:5.1f}   {r['t']}")

    print("\n═══ რა შეიცვლებოდა, მაქსიმუმის ნაცვლად მეორე რომ გვეღო ═══")
    diffs = [r["top"] - r["second"] for r in rows]
    print(f"  საშუალო დანაკლისი: {sum(diffs)/len(diffs):.2f} მ/წმ")
    for name, lim in THR.items():
        now = sum(1 for r in rows if r["top"] >= lim)
        then = sum(1 for r in rows if r["second"] >= lim)
        print(f"  {name:10s} გაფრთხილება: {now:4d} → {then:4d} საათი "
              f"({now-then:+d})")
    print("\n  შეხსენება: 10 შედარებაზე პორტალი ᲣᲙᲕᲔ ᲩᲐᲛᲝᲠᲩᲔᲑᲐ MTA-ს")
    print("  ქროლვაზე 8/10 შემთხვევაში (საშ. −1.69 მ/წმ). დამატებითი")
    print("  ჩამოწევა იმ მიმართულებით წაგვიყვანს, სადაც უკვე ვცდებით.")


if __name__ == "__main__":
    main()
