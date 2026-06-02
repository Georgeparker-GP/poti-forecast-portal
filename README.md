# ⚓ ფოთის პორტი — ამინდის კონსენსუს-პორტალი

48-საათიანი საოპერაციო ამინდის პორტალი GitHub Pages-ზე.
ავტომატური განახლება ყოველ საათში GitHub Actions-ის მეშვეობით.

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

| სახელი | სად მიიღო |
|--------|-----------|
| `WINDY_API_KEY` | windy.com/en/api |
| `STORMGLASS_API_KEY` | stormglass.io |
| `OWM_API_KEY` | openweathermap.org |
| `TELEGRAM_BOT_TOKEN` | @BotFather Telegram-ში |
| `TELEGRAM_CHAT_ID` | @userinfobot Telegram-ში |

> გასაღებების გარეშეც მუშაობს — Open-Meteo უფასოა.

---

### 5. პირველი გაშვება

**Actions**탭 → **Weather Fetch** → **Run workflow** → **Run workflow** ✓

კონსოლში უნდა გამოჩნდეს:
```
Open-Meteo [best_match] ✓
Open-Meteo [gfs_seamless] ✓
Open-Meteo Marine ✓
✓ data.json განახლდა
```

---

## 📁 ფაილების სტრუქტურა

| ფაილი | როლი |
|-------|------|
| `fetch.py` | API-ების გამოძახება + კონსენსუსი |
| `index.html` | პორტალის ინტერფეისი |
| `data.json` | კონსენსუს-პროგნოზი (ავტო-განახლება) |
| `requirements.txt` | Python ბიბლიოთეკები |
| `.github/workflows/fetch.yml` | ავტომატური გაშვება |
| `stormglass_cache.json` | Stormglass კეში (ავტო) |
| `status_cache.json` | Telegram სტატუს-კეში (ავტო) |

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
