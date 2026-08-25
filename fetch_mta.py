#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_mta.py — MTA-ს ბიულეტენების მიღება Gmail-იდან (IMAP).

═══ რატომ Gmail და არა OneDrive ═══
საწყისი გეგმა იყო Power Automate → OneDrive → ანონიმური share-ბმული →
HTTP ჩამოტვირთვა. 2026-08-25-ის ტესტმა აჩვენა, რომ Maersk-ის tenant-ზე
ანონიმური გაზიარება tenant-ის დონეზე გამორთულია — "Anyone with the link"
პარამეტრებში საერთოდ არ არსებობს.

მოქმედი სქემა:
    MTA → potisinoptika@mta.gov.ge → Outlook
      ↓ Power Automate: "Send an email (V2)" დანართებით
    Gmail (ფილტრი: subject "MTA —" → ლეიბლი "MTA", Skip Inbox)
      ↓ ეს სკრიპტი: IMAP-ით კითხულობს
    mta_bulletins/*.pdf
      ↓ mta_ingest.py (უკვე არსებობს)
    mta_log.json

გარემოს ცვლადები:
    GMAIL_USER            — მაგ. gigaparkaia@gmail.com
    GMAIL_APP_PASSWORD    — Google App Password (16 სიმბოლო, ჰარეების გარეშე)

პრინციპი: არასოდეს აგდებს არა-ნულოვან exit კოდს — MTA-ს პაიპლაინი
პორტალის მთავარ განახლებას ვერ უნდა შეაფერხოს.
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

# ─────────────────────────── კონფიგი ───────────────────────────

IMAP_HOST = "imap.gmail.com"
MAILBOX = "MTA"                  # Gmail-ის ლეიბლი = IMAP-ის საქაღალდე
SUBJECT_TAG = "MTA"              # Power Automate-ის პრეფიქსი

DEST_DIR = pathlib.Path("mta_bulletins")
STATE_FILE = pathlib.Path("mta_mail_state.json")

MAX_PER_RUN = 25
MAX_STATE = 400
MAX_BYTES = 25 * 1024 * 1024
LOOKBACK_DAYS = 14               # ამაზე ძველს არ ვეხებით
PDF_MAGIC = b"%PDF"

TBILISI_TZ = timezone(timedelta(hours=4))


def log(msg: str) -> None:
    print(f"[fetch_mta] {msg}", flush=True)


# ─────────────────────────── დამხმარეები ───────────────────────────


def decode_mime(value: str) -> str:
    """MIME-კოდირებული სათაური/ფაილის სახელი → ტექსტი.

    კრიტიკული: MTA-ს ფაილების სახელები ქართულია და base64/quoted-printable
    კოდირებით მოდის. mta_ingest.py ტიპს სწორედ ფაილის სახელით არჩევს
    ("ფაქტიური" / "საშტორმო" / "პროგნოზი"), ამიტომ სახელი სწორად უნდა
    გაიშიფროს — თორემ პარსერი ვერ მიხვდება, რა ტიპისაა.
    """
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def safe_name(name: str) -> str:
    """ფაილის სახელის სანიტიზაცია — ქართული ასოები ნარჩუნდება."""
    name = os.path.basename((name or "").strip())
    # ვტოვებთ ასოებს (ქართულის ჩათვლით), ციფრებს, წერტილს, ხაზგასმას, დეფისს
    name = re.sub(r"[^\w.\-№]+", "_", name, flags=re.UNICODE).strip("._-")
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf" if name else "bulletin.pdf"
    return name[:150]


def load_state() -> list:
    if not STATE_FILE.exists():
        return []
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        seen = data.get("seen", []) if isinstance(data, dict) else data
        return [str(x) for x in seen] if isinstance(seen, list) else []
    except Exception as exc:
        log(f"state წაკითხვის შეცდომა ({exc}) — ცარიელით ვაგრძელებ")
        return []


def save_state(seen: list) -> None:
    try:
        payload = {
            "updated": datetime.now(TBILISI_TZ).strftime("%Y-%m-%d %H:%M"),
            "seen": seen[-MAX_STATE:],
        }
        STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        log(f"state ჩაწერის შეცდომა: {exc}")


def pick_mailbox(mail: imaplib.IMAP4_SSL) -> str:
    """MTA ლეიბლს ვცდით; თუ არ არსებობს — INBOX.

    Gmail ლეიბლს IMAP-ში საქაღალდედ აჩვენებს. თუ ფილტრი ჯერ არ შექმნილა
    ან სახელი სხვაა, ინბოქსზე გადავდივართ, რომ სკრიპტი მაინც იმუშაოს.
    """
    for box in (f'"{MAILBOX}"', "INBOX"):
        try:
            typ, _ = mail.select(box, readonly=False)
            if typ == "OK":
                log(f"საქაღალდე: {box}")
                return box
        except Exception:
            continue
    raise RuntimeError("ვერც MTA და ვერც INBOX გაიხსნა")


# ─────────────────────────── ძირითადი ლოგიკა ───────────────────────────


def extract_pdfs(msg, seen_set: set) -> list:
    """წერილიდან PDF დანართების ამოღება. აბრუნებს [(სახელი, ბაიტები), ...]"""
    out = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = str(part.get("Content-Disposition") or "")
        fname = decode_mime(part.get_filename() or "")
        ctype = (part.get_content_type() or "").lower()

        is_pdf = ctype == "application/pdf" or fname.lower().endswith(".pdf")
        if not is_pdf and "attachment" not in disp.lower():
            continue
        if not is_pdf:
            continue

        try:
            blob = part.get_payload(decode=True)
        except Exception as exc:
            log(f"დანართის დეკოდირება ჩავარდა ({fname}): {exc}")
            continue

        if not blob:
            continue
        if len(blob) > MAX_BYTES:
            log(f"{fname}: ზომა აჭარბებს ლიმიტს — გამოტოვება")
            continue
        if not blob.startswith(PDF_MAGIC):
            log(f"{fname}: არ არის PDF — გამოტოვება")
            continue

        out.append((safe_name(fname), blob))
    return out


def main() -> None:
    user = os.environ.get("GMAIL_USER", "").strip()
    pwd = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()

    if not user or not pwd:
        log("GMAIL_USER / GMAIL_APP_PASSWORD არ არის დაყენებული — გამოტოვება")
        return

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    seen = load_state()
    seen_set = set(seen)

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(user, pwd)
    except imaplib.IMAP4.error as exc:
        log(f"IMAP ავტორიზაცია ჩავარდა: {exc}")
        log("შეამოწმე: App Password სწორია? 2-Step Verification ჩართულია?")
        return
    except Exception as exc:
        log(f"IMAP კავშირი ჩავარდა: {exc}")
        return

    try:
        pick_mailbox(mail)

        since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        # UNSEEN — რომ იგივე წერილი ორჯერ არ დამუშავდეს.
        # SINCE — ძველ არქივს არ ვეხებით.
        typ, data = mail.search(None, f'(UNSEEN SINCE {since})')
        if typ != "OK":
            log("ძებნა ჩავარდა")
            return

        ids = data[0].split()
        if not ids:
            log("ახალი წერილი არ არის")
            return

        log(f"{len(ids)} წაუკითხავი წერილი")
        if len(ids) > MAX_PER_RUN:
            log(f"იზღუდება {MAX_PER_RUN}-მდე")
            ids = ids[-MAX_PER_RUN:]

        saved = 0
        for num in ids:
            try:
                typ, raw = mail.fetch(num, "(RFC822)")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])
            except Exception as exc:
                log(f"წერილის წაკითხვა ჩავარდა: {exc}")
                continue

            subject = decode_mime(msg.get("Subject", ""))
            msg_id = (msg.get("Message-ID") or "").strip() or f"noid-{num.decode()}"

            # მხოლოდ ჩვენი პრეფიქსი (ინბოქსზე გადასვლის შემთხვევისთვის)
            if SUBJECT_TAG not in subject:
                continue

            if msg_id in seen_set:
                continue

            pdfs = extract_pdfs(msg, seen_set)
            if not pdfs:
                log(f"დანართის გარეშე: {subject[:60]}")
                # მაინც ვნიშნავთ — თორემ ყოველ ჯერზე თავიდან წავიკითხავთ
                seen.append(msg_id); seen_set.add(msg_id)
                continue

            for fname, blob in pdfs:
                target = DEST_DIR / fname
                if target.exists():
                    stamp = datetime.now(TBILISI_TZ).strftime("%H%M%S")
                    target = DEST_DIR / f"{target.stem}_{stamp}.pdf"
                try:
                    target.write_bytes(blob)
                    saved += 1
                    log(f"შენახულია: {target.name} ({len(blob)//1024} KB)")
                except Exception as exc:
                    log(f"ჩაწერა ჩავარდა [{fname}]: {exc}")

            seen.append(msg_id); seen_set.add(msg_id)
            # წაკითხულად მონიშვნა — მხოლოდ წარმატებული შენახვის შემდეგ
            try:
                mail.store(num, "+FLAGS", "\\Seen")
            except Exception:
                pass

        save_state(seen)
        log(f"დასრულდა: {saved} PDF → {DEST_DIR}/")

    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"მოულოდნელი შეცდომა: {exc}")
    sys.exit(0)
