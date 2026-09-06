#!/usr/bin/env python3
"""ერთიანი შედარების ფაილი: MTA + ველზე დაკვირვება + პორტალი.

რას აკეთებს:
  1. `mta_log.json`-იდან იღებს პორტალთან შედარებებს (ავტომატური).
  2. `field_obs.txt`-იდან — Giga-ს ხელით შეყვანილ დაკვირვებებს.
  3. `data.json`-ის git-ის ისტორიიდან პოულობს პორტალის რეალურ
     მნიშვნელობას ᲖᲣᲡᲢᲐᲓ იმ საათზე, რომელზეც დაკვირვებაა.
  4. წერს `comparison.md`-ს — ქრონოლოგიური ცხრილი + სტატისტიკა.

⚠ ᲛᲮᲝᲚᲝᲓ ᲘᲙᲘᲗᲮᲐᲕᲡ ᲓᲐ ᲬᲔᲠᲡ ᲐᲜᲒᲐᲠᲘᲨᲡ. კონსენსუსს, ზღვრებს და
   წონებს არ ეხება.

კალიბრაციის წესი (ჟურნალიდან):
  ვალიდურია მდგრადი ქარი ხელსაწყოდან, სიმაღლის შესწორებით.
  ქროლვა და ვიზუალური შეფასება — კონტექსტი, არა კალიბრაცია.

გაშვება (რეპოს ძირში, სრული ისტორიით):
    python3 compare_report.py
"""

import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime

MTA_LOG = "mta_log.json"
FIELD = "field_obs.txt"
DATA = "data.json"
OUT = "comparison.md"

CRANE_H = 1.12          # 25 მ → 10 მ ეკვივალენტი

# პარამეტრი → data.json-ის ველი, ერთეული, ვალიდურია კალიბრაციაში?
PARAM = {
    "wind":   ("wind_speed",    "მ/წმ", True),
    "gust":   ("wind_gusts",    "მ/წმ", False),   # წამიერი — არა კალიბრაცია
    "wave":   ("wave_height",   "მ",    True),
    "precip": ("precipitation", "მმ",   True),
    "vis":    ("visibility_km", "კმ",   True),
    "dir":    ("wind_direction", "°",   True),
    "temp":   ("air_temp",      "°C",   True),
}

VISUAL = ("ვიზუალ", "თვალ", "visual")
CRANE = ("ამწე", "crane")


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout


def portal_index():
    """{'YYYY-MM-DDTHH:00': current-ბლოკი} git-ის ისტორიიდან.

    ვიღებთ `current`-ს და არა `forecast`-ს: current იმ საათის
    nowcast-ია, ანუ ყველაზე ახლოსაა რეალობასთან.
    """
    idx = {}
    for line in sh("git", "log", "--format=%H", "--", DATA).splitlines():
        sha = line.strip()
        if not sha:
            continue
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
    return idx


def parse_value(raw):
    """'1.0' → (1.0, 1.0);  '0.8-1.0' → (0.8, 1.0);  სხვა → None"""
    raw = raw.strip().replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", raw)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", raw)
    if m:
        v = float(m.group(1))
        return v, v
    return None


def read_field(path):
    rows, bad = [], []
    if not pathlib.Path(path).exists():
        return rows, bad
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            bad.append((n, line, "სვეტი 4-ზე ნაკლებია"))
            continue
        t_raw, param, val_raw, src = parts[0], parts[1].lower(), parts[2], parts[3]
        note = parts[4] if len(parts) > 4 else ""

        if param not in PARAM:
            bad.append((n, line, f"უცნობი პარამეტრი: {param}"))
            continue
        try:
            dt = datetime.strptime(t_raw[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            bad.append((n, line, "დროის ფორმატი"))
            continue
        v = parse_value(val_raw)
        if v is None:
            bad.append((n, line, f"მნიშვნელობა ვერ წაიკითხა: {val_raw}"))
            continue

        lo, hi = v
        corrected = False
        if param in ("wind", "gust") and any(k in src.lower() for k in CRANE):
            lo, hi = lo / CRANE_H, hi / CRANE_H
            corrected = True

        is_visual = any(k in src.lower() for k in VISUAL)
        valid = PARAM[param][2] and not is_visual

        rows.append({
            "t": dt.strftime("%Y-%m-%dT%H:00"),
            "param": param, "lo": lo, "hi": hi, "raw": val_raw,
            "src": src, "note": note,
            "corrected": corrected, "visual": is_visual, "valid": valid,
        })
    rows.sort(key=lambda r: r["t"])
    return rows, bad


def read_mta(path):
    if not pathlib.Path(path).exists():
        return []
    d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    out = []
    for e in d.get("entries", []):
        vs = e.get("vs_portal") or {}
        if not vs:
            continue
        date, time = e.get("date", ""), e.get("time", "")
        m = re.search(r"(\d{1,2})\s*/\s*\S+\s*/\s*(\d{4})", date or "")
        stamp = e.get("portal_time", "")
        out.append({
            "t": stamp[:16] if "T" in stamp else stamp,
            "bno": e.get("bulletin_no", "?"),
            "exact": e.get("portal_hour_exact") is True,
            "vs": vs,
        })
    out.sort(key=lambda r: r["t"])
    return out


def fmt(v, n=2):
    return "--" if v is None else f"{float(v):.{n}f}"


def main():
    print("პორტალის ისტორიის კითხვა git-იდან…")
    idx = portal_index()
    print(f"  საათი: {len(idx)}")

    field, bad = read_field(FIELD)
    mta = read_mta(MTA_LOG)
    print(f"ველზე დაკვირვება: {len(field)}   MTA შედარება: {len(mta)}")

    if bad:
        print("\n⚠ წაუკითხავი სტრიქონები field_obs.txt-ში:")
        for n, line, why in bad:
            print(f"  {n}: {why}  —  {line[:60]}")

    L = []
    L.append("# შედარების ანგარიში")
    L.append("")
    L.append(f"აგებულია `compare_report.py`-ით · "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    L.append("")
    L.append(f"პორტალის საათი ისტორიაში: **{len(idx)}** · "
             f"MTA შედარება: **{len(mta)}** · "
             f"ველზე დაკვირვება: **{len(field)}**")
    L.append("")
    L.append("> ვალიდურია მდგრადი ქარი ხელსაწყოდან სიმაღლის შესწორებით. "
             "ქროლვა და ვიზუალური შეფასება კონტექსტია, არა კალიბრაცია.")
    L.append("")

    # ── ველზე დაკვირვება ──
    L.append("## ველზე დაკვირვება vs პორტალი")
    L.append("")
    if not field:
        L.append("_ჩანაწერი არ არის._")
    else:
        L.append("| დრო | პარამეტრი | ნანახი | პორტალი | სხვაობა | წყარო | კალიბრ. | შენიშვნა |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in field:
            key, unit, _ = PARAM[r["param"]]
            cur = idx.get(r["t"])
            pv = cur.get(key) if cur else None
            shown = fmt(r["lo"]) if r["lo"] == r["hi"] else f"{fmt(r['lo'])}–{fmt(r['hi'])}"
            if r["corrected"]:
                shown += f" ⇣"          # სიმაღლის შესწორება გამოყენებულია
            if pv is None:
                delta = "—"
            elif r["lo"] <= float(pv) <= r["hi"]:
                delta = "**0 (ხვდება)**"
            elif float(pv) < r["lo"]:
                delta = f"−{fmt(r['lo'] - float(pv))}"
            else:
                delta = f"+{fmt(float(pv) - r['hi'])}"
            L.append(f"| {r['t'].replace('T',' ')} | {r['param']} | "
                     f"{shown} {unit} | {fmt(pv)} {unit} | {delta} | "
                     f"{r['src']} | {'✅' if r['valid'] else '—'} | {r['note']} |")
        L.append("")
        L.append("⇣ = ამწის 25 მ → 10 მ ეკვივალენტი (÷1.12)")
        miss = [r for r in field if r["t"] not in idx]
        if miss:
            L.append("")
            L.append(f"⚠ პორტალის ჩანაწერი ვერ მოიძებნა {len(miss)} საათზე: "
                     + ", ".join(r["t"].replace("T", " ") for r in miss))
    L.append("")

    # ── MTA ──
    L.append("## MTA vs პორტალი")
    L.append("")
    exact = [m for m in mta if m["exact"]]
    L.append(f"ზუსტი შედარება (`portal_hour_exact: true`): "
             f"**{len(exact)}** / {len(mta)}")
    L.append("")
    L.append("| დრო | ბიულეტენი | ქროლვა Δ | მდგრადი Δ | ტალღა Δ | მიმართ. Δ | ნალექი |")
    L.append("|---|---|---|---|---|---|---|")
    for m in mta:
        vs = m["vs"]
        mark = "" if m["exact"] else " ⚠"
        L.append(f"| {m['t'].replace('T',' ')}{mark} | {m['bno']} | "
                 f"{fmt(vs.get('wind_delta'),1)} | {fmt(vs.get('wind_avg_delta'),2)} | "
                 f"{fmt(vs.get('wave_delta'))} | {fmt(vs.get('dir_delta'),0)} | "
                 f"{vs.get('precip_verdict','—')} |")
    L.append("")
    L.append("⚠ = საათი ზუსტად არ ემთხვევა, კალიბრაციაში არ ითვლება. "
             "Δ დადებითი ნიშნავს, რომ პორტალი ᲓᲐᲑᲚᲐᲐ.")
    L.append("")

    # ── სტატისტიკა ──
    if exact:
        L.append("## სტატისტიკა (მხოლოდ ზუსტი შედარებები)")
        L.append("")
        L.append("| მაჩვენებელი | n | საშუალო Δ | ერთმხრივია? |")
        L.append("|---|---|---|---|")
        for key, label in [("wind_delta", "ქროლვა"),
                           ("wind_avg_delta", "მდგრადი ქარი"),
                           ("wave_delta", "ტალღა"),
                           ("dir_delta", "მიმართულება"),
                           ("sea_temp_delta", "ზღვის ტემპ."),
                           ("air_temp_delta", "ჰაერის ტემპ.")]:
            vals = [m["vs"][key] for m in exact if key in m["vs"]]
            if not vals:
                continue
            pos = sum(1 for v in vals if v > 0)
            one = "დიახ" if pos in (0, len(vals)) else f"არა ({pos}/{len(vals)})"
            L.append(f"| {label} | {len(vals)} | "
                     f"{sum(vals)/len(vals):+.2f} | {one} |")
        L.append("")

    pathlib.Path(OUT).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n✅ დაიწერა: {OUT}  ({len(L)} სტრიქონი)")


if __name__ == "__main__":
    main()
