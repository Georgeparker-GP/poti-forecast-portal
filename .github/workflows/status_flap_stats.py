#!/usr/bin/env python3
"""სტატუსის ცვლილებები და "პინგ-პონგი" git-ის ისტორიიდან.

კითხვა: `send_telegram` ყოველ სტატუსის ცვლილებაზე აგზავნის შეტყობინებას
(ორივე მიმართულებით). რამდენად ხშირად ხდება ზღვართან ციმციმი და
რამდენ ზედმეტ შეტყობინებას ქმნის?

⚠ ᲛᲮᲝᲚᲝᲓ ᲐᲜᲐᲚᲘᲖᲘᲐ. არაფერს არ ცვლის.

მნიშვნელოვანი დათქმა: ვითვლით `current.status`-ის ცვლილებას
ᲡᲐᲐᲗᲝᲑᲠᲘᲕ ᲡᲔᲠᲘᲐᲖᲔ. რეალურად შეტყობინება commit-ზე იგზავნება და თუ
რომელიმე გაშვება ჩავარდა, ცვლილება შეიძლება შეუმჩნეველი დარჩენილიყო.
ანუ ეს ᲖᲔᲓᲐ შეფასებაა.

გაშვება (რეპოს ძირში, სრული ისტორიით):
    python3 status_flap_stats.py
"""

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime

FILE = "data.json"
ORDER = {"operational": 0, "barge": 1, "vessel": 2, "suspended": 3}

# რამდენ საათს უნდა გაძლოს უფრო მსუბუქმა სტატუსმა, რომ
# დაბრუნება "ნამდვილად" ჩაითვალოს
DWELLS = (1, 2, 3, 4, 6)


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def series():
    rows, seen = [], set()
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
        cur = d.get("current") or {}
        t, st = cur.get("time"), cur.get("status")
        if not t or not st or t in seen:
            continue
        seen.add(t)
        rows.append({
            "t": t, "st": st,
            "w": cur.get("wind_speed"), "g": cur.get("wind_gusts"),
            "wave": cur.get("wave_height"), "vis": cur.get("visibility_km"),
        })
    rows.sort(key=lambda r: r["t"])
    return rows


def hours_between(a, b):
    fa = datetime.strptime(a[:16], "%Y-%m-%dT%H:%M")
    fb = datetime.strptime(b[:16], "%Y-%m-%dT%H:%M")
    return (fb - fa).total_seconds() / 3600.0


def main():
    rows = series()
    if len(rows) < 10:
        sys.exit("ცოტა მონაცემია. საჭიროა სრული ისტორია (fetch-depth: 0).")

    print(f"საათი: {len(rows)}")
    print(f"პერიოდი: {rows[0]['t']}  →  {rows[-1]['t']}\n")

    dist = Counter(r["st"] for r in rows)
    print("═══ სტატუსების განაწილება ═══")
    for st in sorted(dist, key=lambda s: ORDER.get(s, 9)):
        print(f"  {st:12s} {dist[st]:5d}  ({dist[st]/len(rows)*100:5.2f}%)")

    # ── ცვლილებები ──
    changes = []
    for i in range(1, len(rows)):
        if rows[i]["st"] != rows[i - 1]["st"]:
            changes.append({
                "i": i, "t": rows[i]["t"],
                "frm": rows[i - 1]["st"], "to": rows[i]["st"],
                "gap": hours_between(rows[i - 1]["t"], rows[i]["t"]),
            })

    print(f"\n═══ სტატუსის ცვლილება: {len(changes)} ═══")
    print("(= ამდენი Telegram შეტყობინება, თუ ყველა გაშვება წარმატებული იყო)")
    up = sum(1 for c in changes if ORDER.get(c["to"], 0) > ORDER.get(c["frm"], 0))
    print(f"  გამკაცრება: {up}   შერბილება: {len(changes)-up}")
    for (a, b), n in Counter((c["frm"], c["to"]) for c in changes).most_common():
        print(f"    {a:12s} → {b:12s} {n:4d}")

    # ── ეპიზოდები: რამდენ ხანს ძლებს არა-operational ──
    print("\n═══ არა-operational ეპიზოდები ═══")
    eps, cur_ep = [], None
    for r in rows:
        if r["st"] != "operational":
            if cur_ep is None:
                cur_ep = {"start": r["t"], "st": r["st"], "n": 0, "peak": 0}
            cur_ep["n"] += 1
            cur_ep["peak"] = max(cur_ep["peak"], r["g"] or 0)
            if ORDER.get(r["st"], 0) > ORDER.get(cur_ep["st"], 0):
                cur_ep["st"] = r["st"]
        elif cur_ep:
            eps.append(cur_ep)
            cur_ep = None
    if cur_ep:
        eps.append(cur_ep)

    if not eps:
        print("  არცერთი. ციმციმის საკითხი ამ მონაცემზე არ დგას.")
    else:
        print(f"  სულ: {len(eps)} ეპიზოდი")
        by_len = Counter(e["n"] for e in eps)
        for n in sorted(by_len):
            print(f"    {n:3d} საათიანი: {by_len[n]:3d} ეპიზოდი")
        short = sum(1 for e in eps if e["n"] <= 2)
        print(f"\n  ᲛᲝᲙᲚᲔ (≤2 სთ): {short}/{len(eps)} "
              f"({short/len(eps)*100:.0f}%) — ესენი ქმნიან ხმაურს")
        print("\n  ყველა ეპიზოდი:")
        for e in eps:
            print(f"    {e['start']}  {e['st']:10s} {e['n']:3d} სთ  "
                  f"პიკი {e['peak']:.1f} მ/წმ")

    # ── დაბრუნების დაყოვნება: რამდენი შეტყობინება გადარჩებოდა ──
    print("\n═══ თუ ᲨᲔᲠᲑᲘᲚᲔᲑᲐ N საათს დაელოდება ═══")
    print("(გამკაცრება ყოველთვის მყისიერი რჩება)")
    for dw in DWELLS:
        sent, i, state = 0, 1, rows[0]["st"]
        while i < len(rows):
            new = rows[i]["st"]
            if new == state:
                i += 1
                continue
            if ORDER.get(new, 0) > ORDER.get(state, 0):
                sent += 1
                state = new
                i += 1
                continue
            # შერბილება — უნდა გაძლოს dw საათი
            j, ok = i, True
            while j < min(i + dw, len(rows)):
                if ORDER.get(rows[j]["st"], 0) > ORDER.get(new, 0):
                    ok = False
                    break
                j += 1
            if ok and (j - i) >= min(dw, len(rows) - i):
                sent += 1
                state = new
            i += 1
        saved = len(changes) - sent
        print(f"  N={dw} სთ: {sent:4d} შეტყობინება  "
              f"(დაზოგილი {saved}, {saved/max(len(changes),1)*100:.0f}%)")


if __name__ == "__main__":
    main()
