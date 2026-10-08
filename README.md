# ⚓ ფოთის პორტი — ამინდის კონსენსუს-პორტალი

48-საათიანი საოპერაციო ამინდის პორტალი GitHub Pages-ზე.
ავტომატური განახლება ყოველ საათში GitHub Actions-ის მეშვეობით (ტრიგერი — cron-job.org).

---

## 🚀 GitHub-ზე გამოქვეყნება — ნაბიჯ-ნაბიჯ

### 1. რეპოზიტორიის შექმნა

1. გახსენი **github.com** → შესვლა / რეგისტრაცია
2. მარჯვნივ `+` → **New repository**
3. სახელი: `poti-portal` (ან სხვა)
4. Public ✓ → **Create repository**

---

### 2. ფაილების ატვირთვა

GitHub-ის ვებ ინტერფეისიდან (**Add file → Upload files**):

```
📁 poti-portal/
 ├── index.html
 ├── fetch.py
 ├── requirements.txt
 ├── data.json              ← სადემო (პირველი run-მდე)
 └── .github/
     └── workflows/
         └── fetch.yml
```

> **.github/workflows/fetch.yml** — ეს ყველაზე მნიშვნელოვანია.
> GitHub ამ ფაილს ავტომატურად ამუშავებს.

---

### 3. GitHub Pages-ის ჩართვა

1. რეპოში: **Settings** → **Pages**
2. Source: **Deploy from a branch**
3. Branch: **main** | Folder: **/ (root)**
4. **Save**

✅ 2-3 წუთში პორტალი ხელმისაწვდომი იქნება:
```
https://შენი_username.github.io/poti-portal/
```

---

### 4. API გასაღებები (ოპციონალური)

რეპოში: **Settings → Secrets and variables → Actions → New repository secret**

| სახელი | სად მიიღო / რისთვის |
|--------|-----------|
| `STORMGLASS_API_KEY` | stormglass.io |
| `OWM_API_KEY` | openweathermap.org |
| `TELEGRAM_BOT_TOKEN` | @BotFather Telegram-ში |
| `TELEGRAM_CHAT_ID` | @userinfobot Telegram-ში |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | MTA-ს ბიულეტენების მიღება IMAP-ით |
| `MTA_ALLOWED_SENDERS` | დაშვებული გამგზავნები მძიმით (Power Automate-ის მისამართი). ცარიელზე გამგზავნი არ მოწმდება |
| `CMEMS_USERNAME`, `CMEMS_PASSWORD` | Copernicus Marine (ტალღის ცალკე pipeline) |

> გასაღებების გარეშეც მუშაობს — Open-Meteo უფასოა.

---

### 5. გაშვება

`Update Weather Data` workflow-ს მხოლოდ `workflow_dispatch` ტრიგერი აქვს.
საათში ერთხელ მას cron-job.org უშვებს GitHub API-ით. GitHub-ის `schedule:`
არასტაბილური აღმოჩნდა.

ხელით გაშვება: **Actions → Update Weather Data → Run workflow**. თუ წინა
განახლებიდან 30 წუთი არ გასულა, გაშვება გამოტოვდება. მაშინ `force` ველში
`true` ჩაწერე.

ტესტები:

```
pip install -r requirements.txt
python -m unittest discover -s tests
```

---

## 📁 ფაილების სტრუქტურა

| ფაილი | როლი |
|-------|------|
| `fetch.py` | API-ების გამოძახება + კონსენსუსი → `data.json` |
| `fetch_wave.py` | Copernicus-ის ტალღა → `wave_copernicus.json` (ცალკე workflow) |
| `fetch_mta.py` | MTA-ს PDF ბიულეტენები Gmail-იდან → `mta_bulletins/` |
| `mta_ingest.py`, `mta_parser.py` | ბიულეტენების პარსინგი → `mta_log.json` |
| `compare_report.py`, `backfill_compare.py` | პორტალისა და MTA-ს შედარება → `comparison.md` |
| `*_stats.py` | ერთჯერადი ანალიზები git-ის ისტორიიდან |
| `index.html`, `sw.js`, `manifest.json` | პორტალის ინტერფეისი (PWA) |
| `scene.html` | 3D სცენა (ცალკე იტვირთება) |
| `tests/` | რეგრესიული ტესტები |
| `*_cache.json`, `mta_mail_state.json` | მდგომარეობა და ქეშები (ავტომატური) |

საოპერაციო ზღვრები მხოლოდ `fetch.py`-ის `THRESHOLDS`-შია. `data.json`-ში
ისინი `meta.thresholds`-ად იწერება და `index.html` მათ იქიდან კითხულობს.

---

## 🌊 წყაროები

| წყარო | მოდელი | განახლება |
|-------|--------|-----------|
| Open-Meteo | ECMWF + GFS + ICON | ყოველ 1სთ |
| Open-Meteo Marine | ERA5 + GFS | ყოველ 1სთ |
| Stormglass.io | მრავალი მოდელი | ყოველ 3სთ |
| Windy/ECMWF | ECMWF | ყოველ 1სთ |
| OpenWeatherMap | GFS | ყოველ 1სთ |

---

*ფოთის პორტი — APM Terminals Poti © 2026*
