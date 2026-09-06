#!/usr/bin/env python3
"""ატმოსფერული წნევის რეალური განაწილება git-ის ისტორიიდან.

მიზანი: ბანერისა და ცხრილის ფერების ზღვრები ფოთის ნამდვილ რეჟიმს
მოერგოს და არა გლობალურ სახელმძღვანელოს. ჩემი პირველი ვარიანტი
(1005 / 1025) სავარაუდოდ ვერასოდეს გადაიკვეთება — ეს ამას შეამოწმებს.

წყარო: data.json-ის ყოველი commit. ვიღებთ `current.pressure`-ს
(და `models_now`-ს, თუ იქ სხვა მნიშვნელობაა).

⚠ ᲛᲮᲝᲚᲝᲓ ᲐᲜᲐᲚᲘᲖᲘᲐ. არაფერს არ ცვლის — მხოლოდ ბეჭდავს.

გაშვება (რეპოს ძირში, სრული ისტორიით):
    git fetch --unshallow 2>/dev/null || true
    python3 pressure_stats.py
"""

import json
import subprocess
import sys
from datetime import datetime

FILE = "data.json"


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def commits():
    out = sh("git", "log", "--format=%H %cI", "--", FILE)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            yield parts[0], parts[1]


def pressure_at(sha):
    raw = sh("git", "show", f"{sha}:{FILE}")
    if not raw.strip():
        return None, None
    try:
        d = json.loads(raw)
    except Exception:
        return None, None
    cur = d.get("current") or {}
    p = cur.get("pressure")
    t = cur.get("time")
    return (float(p) if p is not None else None), t


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = (len(sorted_vals) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def main():
    seen, rows = set(), []
    n_commits = 0
    for sha, when in commits():
        n_commits += 1
        p, t = pressure_at(sha)
        if p is None:
            continue
        key = t or when
        if key in seen:          # ერთი საათი — ერთი ჩანაწერი
            continue
        seen.add(key)
        rows.append((key, p))

    print(f"commit-ები data.json-ზე: {n_commits}")
    print(f"უნიკალური საათი წნევით: {len(rows)}\n")
    if len(rows) < 50:
        print("ძალიან ცოტა მონაცემია. თუ რეპო shallow-ია, ჯერ გაუშვი:")
        print("    git fetch --unshallow")
        if not rows:
            sys.exit(1)

    rows.sort()
    vals = sorted(p for _, p in rows)
    print(f"პერიოდი: {rows[0][0]}  →  {rows[-1][0]}\n")

    print("═══ განაწილება (hPa) ═══")
    print(f"  მინიმუმი   {min(vals):8.1f}")
    for q in (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99):
        print(f"  p{int(q*100):<3d}       {pct(vals, q):8.1f}")
    print(f"  მაქსიმუმი  {max(vals):8.1f}")
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    print(f"\n  საშუალო    {mean:8.1f}   σ = {var ** 0.5:.2f}")

    print("\n═══ ამჟამინდელი ზღვრები (1005 / 1025) ═══")
    lo = sum(1 for v in vals if v < 1005)
    hi = sum(1 for v in vals if v > 1025)
    print(f"  <1005 (წითელი): {lo:5d}  ({lo/len(vals)*100:.2f}%)")
    print(f"  >1025 (ლურჯი):  {hi:5d}  ({hi/len(vals)*100:.2f}%)")
    if lo == 0 or hi == 0:
        print("  ⚠ ერთი ან ორივე ფერი ვერასოდეს გამოჩნდება.")

    print("\n═══ შემოთავაზება ═══")
    p10, p90 = pct(vals, 0.10), pct(vals, 0.90)
    p05, p95 = pct(vals, 0.05), pct(vals, 0.95)
    print(f"  p10/p90 → {p10:.0f} / {p90:.0f}   (20% შემთხვევა ფერდება)")
    print(f"  p05/p95 → {p05:.0f} / {p95:.0f}   (10% შემთხვევა ფერდება)")
    print("  p10/p90 ჯობს, თუ გინდა ფერი რეგულარულად ჩანდეს;")
    print("  p05/p95 — თუ გინდა მხოლოდ მართლა უჩვეულო ამინდი გამოირჩეოდეს.")

    print("\n═══ ყველაზე დაბალი 10 საათი ═══")
    for t, p in sorted(rows, key=lambda r: r[1])[:10]:
        print(f"  {p:7.1f}   {t}")
    print("\n═══ ყველაზე მაღალი 10 საათი ═══")
    for t, p in sorted(rows, key=lambda r: -r[1])[:10]:
        print(f"  {p:7.1f}   {t}")


if __name__ == "__main__":
    main()
