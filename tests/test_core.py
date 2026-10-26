"""რეგრესიული ტესტები — კონსენსუსი, სტატუსი, MTA-ს მიღება.

გაშვება:  python -m unittest discover -s tests
"""

import email
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fetch          # noqa: E402
import fetch_mta      # noqa: E402
import mta_ingest     # noqa: E402
import mta_parser     # noqa: E402


def _hours(n, start=0, **fields):
    return [{"time": f"2026-10-01T{start + i:02d}:00", **fields} for i in range(n)]


class ComputeStatusTest(unittest.TestCase):
    def test_ladder(self):
        T = fetch.THRESHOLDS
        self.assertEqual(fetch._compute_status(1, 2, 0.2, 10)[0], "operational")
        # ბორნის საფეხური მოხსნილია (2026-10-13): 10–17 მ/წმ — სტანდარტული რეჟიმი
        self.assertNotIn("wind_barge", T)
        self.assertEqual(fetch._compute_status(5, 12.0, 0.2, 10)[0], "operational")
        self.assertEqual(fetch._compute_status(5, 16.9, 0.2, 10)[0], "operational")
        self.assertEqual(fetch._compute_status(5, T["wind_vessel"], 0.2, 10)[0], "vessel")
        self.assertEqual(fetch._compute_status(5, T["wind_suspended"], 0.2, 10)[0], "suspended")
        self.assertEqual(fetch._compute_status(1, 2, T["wave_height"], 10)[0], "suspended")

    def test_vessel_fog_limit_2026_10_08(self):
        # 08.10: 140 მ, მანევრირება შეჩერდა; 20.09: ~350 მ, გრძელდებოდა
        self.assertEqual(fetch._compute_status(3, 5, 0.2, 11.68, vis_min=0.14)[0], "vessel")
        self.assertEqual(fetch._compute_status(3, 5, 0.2, 11.0, vis_min=0.35)[0], "operational")
        self.assertEqual(fetch._compute_status(3, 5, 0.2, 11.0, vis_min=0.997)[0], "operational")

    def test_fog_uses_worst_source(self):
        st, _ = fetch._compute_status(1, 2, 0.2, 11.0, vis_min=0.10)
        self.assertEqual(st, "vessel")
        st, _ = fetch._compute_status(1, 2, 0.2, 11.0, vis_min=0.05)
        self.assertEqual(st, "suspended")


class HelpersTest(unittest.TestCase):
    def test_align_to_by_timestamp(self):
        ref = _hours(4)
        src = [{"time": "2026-10-01T02:00", "v": 2}, {"time": "2026-10-01T03:00", "v": 3}]
        out = fetch._align_to(ref, src)
        self.assertEqual([h.get("v") for h in out], [None, None, 2, 3])

    def test_wavg_skips_missing(self):
        a = [{"x": 10.0}]
        b = [{"x": None}]
        self.assertEqual(fetch._wavg([(a, 0.5), (b, 0.5)], 0, "x"), 10.0)


class StormglassTest(unittest.TestCase):
    def test_missing_wind_is_none_not_zero(self):
        raw = {"hours": [{"time": "2026-10-01T00:00:00+00:00",
                          "waveHeight": {"sg": 0.8}}]}
        h = fetch.parse_stormglass(raw)[0]
        self.assertIsNone(h["wind_speed"])
        self.assertIsNone(h["wind_gusts"])
        self.assertIsNone(h["wind_direction"])
        self.assertEqual(h["wave_height"], 0.8)
        self.assertEqual(h["time"], "2026-10-01T04:00")   # UTC → თბილისი

    def test_missing_wind_does_not_drag_average_down(self):
        best = _hours(2, wind_speed=10.0, wind_gusts=12.0, wind_direction=270.0,
                      precipitation=0.0, visibility_km=10.0)
        sg = _hours(2, wind_speed=None, wind_gusts=None, wind_direction=None,
                    wave_height=0.5)
        # ელიტური აუზი ცარიელია → დეგრადირებული რეჟიმი, სადაც SG მონაწილეობს
        out = fetch.compute_consensus(best, None, None, None, None, sg, None, None)
        self.assertEqual(out[0]["wind_pool"], "degraded")
        self.assertEqual(out[0]["wind_speed_raw"], 10.0)
        self.assertEqual(out[0]["source_count"], 1)


class ConsensusTest(unittest.TestCase):
    def test_duplicate_source_counted_once(self):
        src = _hours(2, wind_speed=5.0, wind_gusts=7.0, wind_direction=90.0,
                     precipitation=0.0, visibility_km=10.0)
        out = fetch.compute_consensus(src, None, None, src, None, None, None, None)
        self.assertEqual(out[0]["source_count"], 1)

    def test_identical_values_from_two_models_counted_once(self):
        # best_match ხშირად ICON-EU-ს ზუსტ ასლს აბრუნებს (სხვადასხვა სია)
        rec = dict(wind_speed=3.0, wind_gusts=4.5, wind_direction=69.0,
                   precipitation=1.0, visibility_km=11.66, air_temp=13.7)
        best, icon = _hours(1, **rec), _hours(1, **rec)
        gfs = _hours(1, **{**rec, "wind_speed": 4.5, "precipitation": 0.0})
        out = fetch.compute_consensus(best, gfs, icon, None, None, None, None, None)
        self.assertEqual(out[0]["precip_total"], 2)       # best + gfs, icon არა
        self.assertEqual(out[0]["precip_agreement"], 63)  # 0.22 / (0.22 + 0.13)

    def test_different_values_both_counted(self):
        base = dict(wind_speed=3.0, wind_gusts=4.5, wind_direction=69.0,
                    precipitation=1.0, visibility_km=11.66, air_temp=13.7)
        best = _hours(1, **base)
        icon = _hours(1, **{**base, "air_temp": 13.6})
        out = fetch.compute_consensus(best, None, icon, None, None, None, None, None)
        self.assertEqual(out[0]["precip_total"], 2)

    def test_output_carries_thresholds(self):
        src = _hours(2, wind_speed=5.0, wind_gusts=7.0, wind_direction=90.0,
                     precipitation=0.0, visibility_km=10.0)
        cons = fetch.compute_consensus(src, None, None, None, None, None, None, None)
        with mock.patch.object(fetch, "load_mta_advisory", return_value=None):
            out = fetch.build_output(cons, ["x"])
        self.assertEqual(out["meta"]["thresholds"], fetch.THRESHOLDS)


class MainTest(unittest.TestCase):
    def test_throttle_skips_recent_run(self):
        with mock.patch.dict(os.environ, {"FORCE_REFRESH": "false"}), \
             mock.patch.object(fetch, "_minutes_since_last_update", return_value=10), \
             mock.patch.object(fetch, "fetch_open_meteo_atmosphere") as fo:
            fetch.main()
        fo.assert_not_called()

    def test_regular_hour_not_throttled(self):
        # საათობრივი გაშვება რიგში დაყოვნებისას 55 წუთზე ადრეც მოდის
        with mock.patch.dict(os.environ, {"FORCE_REFRESH": "false"}), \
             mock.patch.object(fetch, "_minutes_since_last_update", return_value=52), \
             mock.patch.object(fetch, "fetch_open_meteo_atmosphere",
                               side_effect=RuntimeError("stop")) as fo, \
             mock.patch.object(fetch, "send_failure_alert"):
            with self.assertRaises(SystemExit):
                fetch.main()
        fo.assert_called()

    def test_failure_alert_is_html_escaped(self):
        sent = []
        with mock.patch.object(fetch, "_minutes_since_last_update", return_value=None), \
             mock.patch.object(fetch, "fetch_open_meteo_atmosphere",
                               side_effect=TypeError("'<' not supported between <a> & b")), \
             mock.patch.object(fetch, "send_failure_alert", side_effect=sent.append):
            with self.assertRaises(SystemExit):
                fetch.main()
        self.assertEqual(len(sent), 1)
        self.assertIn("&#x27;&lt;&#x27; not supported between &lt;a&gt; &amp; b", sent[0])
        self.assertNotIn("<a>", sent[0])


def _hdr(subject, msg_id, sender="flow@example.com"):
    return email.message_from_string(
        f"Subject: {subject}\nMessage-ID: {msg_id}\nFrom: Flow <{sender}>\nDate: x\n\n")


class MtaSelectionTest(unittest.TestCase):
    def test_cap_applies_only_to_unseen(self):
        # 70 უკვე დამუშავებული ძველი + 5 ახალი: ძველი ლოგიკა ახლებამდე ვერ აღწევდა
        headers = [(str(i).encode(), _hdr("MTA — ბიულეტენი", f"<m{i}>")) for i in range(75)]
        seen = {f"<m{i}>" for i in range(70)}
        cands, skipped, rejected = fetch_mta.select_candidates(headers, seen, set())
        self.assertEqual([c[1] for c in cands], [f"<m{i}>" for i in range(70, 75)])
        self.assertEqual(skipped, 70)
        self.assertEqual(rejected, [])

    def test_sender_allowlist(self):
        headers = [(b"1", _hdr("MTA — a", "<a>", "flow@example.com")),
                   (b"2", _hdr("MTA — b", "<b>", "evil@example.org")),
                   (b"3", _hdr("სხვა", "<c>", "flow@example.com"))]
        cands, _, rejected = fetch_mta.select_candidates(
            headers, set(), {"flow@example.com"})
        self.assertEqual([c[1] for c in cands], ["<a>"])
        self.assertEqual(rejected, [("MTA — b", "evil@example.org")])

    def test_fetch_headers_parses_imap_response(self):
        mail = mock.Mock()
        mail.fetch.return_value = ("OK", [
            (b"2 (BODY[HEADER.FIELDS (SUBJECT DATE MESSAGE-ID FROM)] {30}",
             b"Subject: MTA b\r\nMessage-ID: <b>\r\n\r\n"), b")",
            (b"1 (BODY[HEADER.FIELDS (SUBJECT DATE MESSAGE-ID FROM)] {30}",
             b"Subject: MTA a\r\nMessage-ID: <a>\r\n\r\n"), b")",
        ])
        out = fetch_mta.fetch_headers(mail, [b"1", b"2"])
        self.assertEqual([(n, h["Message-ID"]) for n, h in out], [(b"1", "<a>"), (b"2", "<b>")])
        self.assertIn("PEEK", mail.fetch.call_args[0][1])

    def test_message_key_without_id_is_stable(self):
        m = email.message_from_string("Subject: MTA\nDate: Mon\n\n")
        self.assertEqual(fetch_mta.message_key(m, "MTA"), fetch_mta.message_key(m, "MTA"))
        self.assertTrue(fetch_mta.message_key(m, "MTA").startswith("noid-"))


class MtaParserTest(unittest.TestCase):
    def test_num_accepts_comma_decimals(self):
        self.assertEqual(mta_parser._num("20,2"), 20.2)
        self.assertEqual(mta_parser._num("1015,5"), 1015.5)
        self.assertEqual(mta_parser._num("1,5-2,0"), (1.5, 2.0))
        self.assertEqual(mta_parser._num("155-225"), (155.0, 225.0))
        self.assertEqual(mta_parser._num("ცვალებადი"), "ცვალებადი")
        self.assertIsNone(mta_parser._num("  "))


# ─── MTA-ს საშტორმო: რეალური ტექსტები (14/7539, 14/7543, 14/7588) ───
FOG_TXT = """№ 14/7539
საშტორმო გაფრთხილება
ფოთი-ყულევის აკვატორიაში მომდევნო 2-3 საათში შენარჩუნდება ნისლი,
STORM WARNING
In the area of Poti-Kulevi In the next 2-3 hours will be remained fog. time to time heavi fog. visibility 0.1-0.5 miles.
A.W. Poti - visibility 200 meter.
kulevi- 10 miles.
14.09.26წ. 07სთ20წთ.
სინოპტიკოსი"""

FOG_CANCEL_TXT = """№ 14/7543
საშტორმო გაფრთხილება №14/7539 ის გაუქმება.
STORM WARNING №14/7539 Cancelation
In the area of Poti-Kulevi the storm warning № 14/7539 is canceled.
a.w. Poti-Kulevi visibility -10 miles.
14.09.26წ. 10:00სთ.
სინოპტიკოსი"""

WIND_TXT = """№ 14/7588
STORM WARNING
In the area of Poti-Kulevi will be maintained sea 3-4/ 4 state (W.H 80-180/ 125-250cm). Wind NW/W
6-11 m/sec, with ocasionally gusts 12-15 m/sec.
A.W. (Poti)- SW 4-5 m/sec.
(Kulevi) - S 3-4 m/sec.
Sea 3-4 (w.h. 106-139 cm.).
15.09.26 წ. 18:35 სთ.
სინოპტიკოსი"""


def _fake_pdf(text):
    page = mock.Mock()
    page.extract_text.return_value = text
    page.extract_tables.return_value = []
    pdf = mock.MagicMock()
    pdf.__enter__.return_value.pages = [page]
    return pdf


class MtaStormParserTest(unittest.TestCase):
    def _parse(self, text, subject):
        with mock.patch.object(mta_parser.pdfplumber, "open", return_value=_fake_pdf(text)):
            return mta_parser.parse_mta_pdf("x.pdf", subject)

    def test_fog_warning_is_not_empty(self):
        # 10.10: ნისლის საშტორმო `poti: {}`-ად იწერებოდა
        r = self._parse(FOG_TXT, "საშტორმო გაფრთხილება")
        self.assertEqual(r["type"], "storm_warning")
        self.assertEqual(r["phenomena"], ["fog"])
        self.assertEqual(r["forecast"]["vis_km"], (0.185, 0.926))
        self.assertEqual(r["poti"]["vis_km"], 0.2)
        self.assertEqual((r["date"], r["time"]), ("14/09/2026", "07:20"))
        self.assertIn("visibility 200 meter", r["text_eng"])

    def test_cancel_with_number_sign(self):
        r = self._parse(FOG_CANCEL_TXT, "საშტორმო გაფრთხილების გაუქმება")
        self.assertEqual(r["type"], "storm_cancel")
        self.assertEqual(r["cancels"], "14/7539")
        self.assertEqual(r["bulletin_no"], "14/7543")
        self.assertEqual((r["date"], r["time"]), ("14/09/2026", "10:00"))

    def test_wind_warning_alternate_format(self):
        r = self._parse(WIND_TXT, "საშტორმო გაფრთხილება")
        self.assertEqual(r["forecast"]["wind_range"], (6.0, 11.0))
        self.assertEqual(r["forecast"]["gust_max"], 15.0)
        self.assertEqual(r["forecast"]["wave_cm"], (80.0, 250.0))
        self.assertEqual(r["poti"]["wind_range"], (4.0, 5.0))
        self.assertEqual(r["phenomena"], ["wind", "sea"])


class MtaIngestStormTest(unittest.TestCase):
    CANCEL = "საშტორმო_გაფრთხილება_ფოთი_№_14.7539_გაუქმება_14.09.2026წ._10.00_სთ..pdf"
    WARN = "საშტორმო_გაფრთხილება_ფოთი_№_14.8347_10.10.2026წ._05.40_სთ..pdf"

    def test_cancel_routed_by_filename(self):
        self.assertEqual(mta_ingest._subject_from_name(self.CANCEL),
                         "საშტორმო გაფრთხილების გაუქმება")
        self.assertEqual(mta_ingest._subject_from_name(self.WARN), "საშტორმო გაფრთხილება")

    def test_dt_from_name(self):
        self.assertEqual(mta_ingest._dt_from_name(self.WARN), ("10/10/2026", "05:40"))
        self.assertEqual(mta_ingest._dt_from_name(
            "საშტორმო_გაფრთხილება_ფოთი_№_14.7829_23.09.2026წ._შეცვლა_14.05სთ..pdf"),
            ("23/09/2026", "14:05"))

    def test_repair_legacy_entries_idempotent(self):
        log = {"entries": [
            {"type": "storm_warning", "source_file": self.WARN, "bulletin_no": "14/8347"},
            {"type": "storm_warning", "source_file": self.CANCEL, "bulletin_no": "14/7539"},
        ]}
        self.assertGreater(mta_ingest._repair_storm_entries(log), 0)
        w, c = log["entries"]
        self.assertEqual((w["type"], w["date"], w["time"]), ("storm_warning", "10/10/2026", "05:40"))
        self.assertEqual((c["type"], c["cancels"], c["bulletin_no"]), ("storm_cancel", "14/7539", None))
        self.assertEqual(mta_ingest._repair_storm_entries(log), 0)


class StormAdvisoryTest(unittest.TestCase):
    WARN = {"type": "storm_warning", "bulletin_no": "14/7539", "date": "14/09/2026",
            "time": "07:20", "phenomena": ["fog"],
            "forecast": {"vis_km": [0.185, 0.926]}, "poti": {"vis_km": 0.2}}
    CANCEL = {"type": "storm_cancel", "cancels": "14/7539", "date": "14/09/2026", "time": "10:00"}

    @staticmethod
    def _at(s):
        from datetime import datetime
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=fetch.TBILISI_TZ)

    def test_active_until_cancelled(self):
        E = [self.WARN, self.CANCEL]
        a = fetch._storm_advisory(E, self._at("2026-09-14 08:00"))
        self.assertEqual(a["kind"], "storm")
        self.assertEqual(a["issued"], "2026-09-14 07:20")
        self.assertEqual(a["notes"], ["საშტორმო გაფრთხილება: ნისლი",
                                      "ხილვადობა 185–926 მ", "ფოთი ფაქტობრივად 200 მ"])
        self.assertIsNone(fetch._storm_advisory(E, self._at("2026-09-14 10:05")))

    def test_expires_without_cancel(self):
        # ვადა არ წერია და გაუქმება არ მოსულა — 48 სთ სათადარიგო ლიმიტი
        self.assertIsNotNone(fetch._storm_advisory([self.WARN], self._at("2026-09-15 20:00")))
        self.assertIsNone(fetch._storm_advisory([self.WARN], self._at("2026-09-16 08:00")))

    def test_explicit_validity_keeps_banner(self):
        # 14/8372: 09:55-ზე, "until the second half of October 13" — 12 სთ-ში არ უნდა გაქრეს
        w = {"type": "storm_warning", "bulletin_no": "14/8372", "date": "11/10/2026",
             "time": "09:55", "phenomena": ["wind"], "valid_until": "2026-10-13 23:59",
             "forecast": {"wind_range": [10, 15], "gust_max": 28}, "poti": {"wind_range": [12, 16]}}
        a = fetch._storm_advisory([w], self._at("2026-10-11 23:00"))
        self.assertEqual(a["valid_until"], "2026-10-13 23:59")
        self.assertIn("ქარი 10–15 მ/წმ, დაქროლვა 28", a["notes"])
        self.assertIsNone(fetch._storm_advisory([w], self._at("2026-10-14 03:00")))

    def test_other_area_ignored(self):
        w = {**self.WARN, "area": "batumi"}
        self.assertIsNone(fetch._storm_advisory([w], self._at("2026-09-14 08:00")))

    def test_advisory_only_updates_banner(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            out, lg = os.path.join(d, "data.json"), os.path.join(d, "mta_log.json")
            json.dump({"meta": {}, "mta_advisory": None}, open(out, "w"))
            json.dump({"entries": [self.WARN]}, open(lg, "w"))
            with mock.patch.object(fetch, "OUTPUT_FILE", out), \
                 mock.patch.object(fetch, "MTA_LOG_FILE", lg), \
                 mock.patch.object(fetch, "datetime", wraps=fetch.datetime) as dt:
                dt.now.return_value = self._at("2026-09-14 08:00")
                fetch.refresh_mta_advisory_only()
            adv = json.load(open(out))["mta_advisory"]
            self.assertEqual(adv["bulletin"], "14/7539")


class StormWindParserTest(unittest.TestCase):
    TXT = ("In the area of Poti-Kulevi, easterly winds of 10-15 m/s will persist until the "
           "second half of October 13, 2026, with occasional gusts of 23-28 m/s. "
           "A.W (Poti) Wind- Easterly 12-16 m/s. (Kulevi) wind- Easterly 4-6 m/s.")

    def test_ms_units_and_validity(self):
        r = mta_parser.storm_fields_from_eng(self.TXT)
        self.assertEqual(r["forecast"], {"wind_range": (10.0, 15.0), "gust_max": 28.0,
                                         "gust_range": (23.0, 28.0)})
        self.assertEqual(r["poti"], {"wind_range": (12.0, 16.0), "wind_dir_txt": "E"})
        self.assertEqual(r["valid_until"], "2026-10-13 23:59")

    def test_repair_backfills_from_text(self):
        log = {"entries": [{"type": "storm_warning", "bulletin_no": "14/8372",
                            "date": "11/10/2026", "time": "09:55",
                            "source_file": "x.pdf", "forecast": {}, "poti": {},
                            "phenomena": ["wind"], "text_eng": self.TXT}]}
        self.assertGreater(mta_ingest._repair_storm_entries(log), 0)
        e = log["entries"][0]
        self.assertEqual(e["valid_until"], "2026-10-13 23:59")
        self.assertEqual(e["forecast"]["gust_max"], 28.0)
        self.assertEqual(mta_ingest._repair_storm_entries(log), 0)


class ForecastGustNoteTest(unittest.TestCase):
    def test_mta_gust_forecast_shown(self):
        import json, tempfile
        e = {"type": "forecast", "bulletin_no": "14/8381",
             "valid": "11/10/ 2026 წ. 22:00 სთ-დან 12/10/ 2026 წ. 10:00 სთ-მდე",
             "poti": {"day": {"wind_speed": "12-17", "gust": [22.0, 27.0]},
                      "night": {"wind_speed": "12-17", "gust": [22.0, 27.0]}}}
        with tempfile.TemporaryDirectory() as d:
            lg = os.path.join(d, "mta_log.json")
            json.dump({"entries": [e]}, open(lg, "w"))
            with mock.patch.object(fetch, "MTA_LOG_FILE", lg), \
                 mock.patch.object(fetch, "datetime", wraps=fetch.datetime) as dt:
                dt.now.return_value = StormAdvisoryTest._at("2026-10-11 23:00")
                a = fetch.load_mta_advisory()
        self.assertEqual(a["notes"], ["პროგნოზით დაქროლვა 22–27 მ/წმ (ქარი 12-17)"])

    def test_weak_gusts_not_shown(self):
        blk = {"day": {"wind_speed": "6-11", "gust": [13.0, 15.0]}}
        e = {"type": "forecast", "valid": "11/10/ 2026 წ. 22:00 სთ-დან 12/10/ 2026 წ. 10:00 სთ-მდე",
             "poti": blk}
        import json, tempfile
        with tempfile.TemporaryDirectory() as d:
            lg = os.path.join(d, "mta_log.json")
            json.dump({"entries": [e]}, open(lg, "w"))
            with mock.patch.object(fetch, "MTA_LOG_FILE", lg), \
                 mock.patch.object(fetch, "datetime", wraps=fetch.datetime) as dt:
                dt.now.return_value = StormAdvisoryTest._at("2026-10-11 23:00")
                self.assertIsNone(fetch.load_mta_advisory())


class GustClampTest(unittest.TestCase):
    def test_gust_not_below_wind(self):
        yr = _hours(1, wind_speed=3.96, wind_gusts=4.95, gust_estimated=True,
                    wind_direction=63.0, precipitation=None, visibility_km=10.0)
        sg = _hours(1, wind_speed=2.2, wind_gusts=3.52, wind_direction=60.0,
                    precipitation=None, visibility_km=24.0)
        out = fetch.compute_consensus(yr, None, None, None, None, sg, None, None)
        self.assertGreaterEqual(out[0]["wind_gusts"], out[0]["wind_speed"])


class GustCalibrationTest(unittest.TestCase):
    def test_consensus_gust_calibrated_raw_kept(self):
        src = _hours(1, wind_speed=8.0, wind_gusts=14.0, wind_direction=270.0,
                     precipitation=0.0, visibility_km=10.0)
        sea = _hours(1, wave_height=0.4, wave_period=4.0, wave_direction=270.0)
        h = fetch.compute_consensus(src, None, None, None, sea, None, None, None)[0]
        self.assertEqual(h["wind_gusts_raw"], 14.0)
        self.assertEqual(h["wind_gusts"], round(14.0 * fetch.GUST_CALIBRATION, 2))
        # საშუალო ქარიც იმავე კოეფიციენტით — gust factor (14/8) უცვლელია
        self.assertEqual(h["wind_speed_raw"], 8.0)
        self.assertEqual(h["wind_speed"], round(8.0 * fetch.GUST_CALIBRATION, 2))
        if fetch.GUST_CALIBRATION >= 1.25:
            self.assertEqual(h["status"], "vessel")         # 14 × 1.3 = 18.2 ≥ 17

    def test_squall_uses_raw_gusts(self):
        cur = {"wind_speed": 4.0, "wind_gusts": 6.5, "wind_gusts_raw": 5.0, "precipitation": 0}
        nxt = {"wind_speed": 8.0, "wind_gusts": 13.0, "wind_gusts_raw": 10.0, "precipitation": 0}
        self.assertIsNone(fetch._detect_squall([cur, nxt], 0))

    def test_daily_summary_calibrated(self):
        day = [{"date": "2026-10-12", "wind_max": 8.0, "gust_max": 14.0}]
        r = fetch.build_daily_summary(day, [])[0]
        self.assertEqual(r["gust_max_raw"], 14.0)
        self.assertEqual(r["gust_max"], round(14.0 * fetch.GUST_CALIBRATION, 2))
        self.assertEqual(r["wind_max_raw"], 8.0)
        self.assertEqual(r["wind_max"], round(8.0 * fetch.GUST_CALIBRATION, 2))

    def test_wave_estimate_uses_raw_wind(self):
        src = _hours(1, wind_speed=12.0, wind_gusts=16.0, wind_direction=270.0,
                     precipitation=0.0, visibility_km=10.0)
        h = fetch.compute_consensus(src, None, None, None, None, None, None, None)[0]
        self.assertEqual(h["wave_height"], round(fetch._estimate_wave_from_wind(12.0, 270.0), 2))


class ForecastValidityFallbackTest(unittest.TestCase):
    def test_until_from_filename(self):
        f = fetch._forecast_until_from_name
        self.assertEqual(f("ამინდის_პროგნოზი_ფოთი_WF_14.8391_12.10.26_10.00_სთ..pdf").strftime("%Y-%m-%d %H:%M"),
                         "2026-10-13 10:00")
        self.assertEqual(f("საღამოს_ამინდის_პროგნოზი_ფოთი_WF_14.7225_2.09.26_22.00_სთ..pdf").strftime("%Y-%m-%d %H:%M"),
                         "2026-09-03 10:00")
        self.assertIsNone(f("3_დღიანი_ამინდის_პროგნოზი_ფოთი-_ყულევი_09.09.26-_11.09.26_წ."))

    def test_forecast_without_valid_still_shown(self):
        import json, tempfile
        e = {"type": "forecast", "bulletin_no": "WF14/8391", "valid": None,
             "source_file": "ამინდის_პროგნოზი_ფოთი_WF_14.8391_12.10.26_10.00_სთ..pdf",
             "poti": {"day": {"wind_speed": "12-17", "gust": [20.0, 25.0]},
                      "night": {"wind_speed": "10-15", "gust": [20.0, 25.0]}}}
        with tempfile.TemporaryDirectory() as d:
            lg = os.path.join(d, "mta_log.json")
            json.dump({"entries": [e]}, open(lg, "w"))
            with mock.patch.object(fetch, "MTA_LOG_FILE", lg), \
                 mock.patch.object(fetch, "datetime", wraps=fetch.datetime) as dt:
                dt.now.return_value = StormAdvisoryTest._at("2026-10-12 18:00")
                a = fetch.load_mta_advisory()
        self.assertEqual(a["valid_until"], "2026-10-13 10:00")
        self.assertTrue(any("20–25" in n for n in sum([n if isinstance(n, list) else [n] for n in a["notes"]], [])))


class NoBargeLevelTest(unittest.TestCase):
    def test_summary_has_no_barge(self):
        src = _hours(3, wind_speed=8.0, wind_gusts=9.0, wind_direction=80.0,
                     precipitation=0.0, visibility_km=10.0)
        cons = fetch.compute_consensus(src, None, None, None, None, None, None, None)
        with mock.patch.object(fetch, "load_mta_advisory", return_value=None):
            out = fetch.build_output(cons, ["x"])
        s = out["summary_24h"]
        self.assertNotIn("barge", s["restricted_hours"])
        self.assertEqual(s["overall_status"], "operational")   # 9 × 1.3 = 11.7 — ადრე „ბორანი“

    def test_old_barge_cache_no_false_notification(self):
        self.assertNotIn(("barge", "operational"), fetch.NOTIFY_TRANSITIONS)
        self.assertIn(("barge", "vessel"), fetch.NOTIFY_TRANSITIONS)


class BilingualNoteTest(unittest.TestCase):
    def test_numbers_borrowed_across_languages(self):
        f = fetch._fmt_mta_note
        self.assertEqual(
            f("wind_speed_note", "დღის შუალედში ცვალებადი 5-10 მ/წმ. In the middle of the day variable",
              {"wind_speed": "7-12"}),
            ("ქარი 7-12, მინაწერი დღის შუალედში ცვალებადი 5-10 მ/წმ",
             "Wind 7-12, note In the middle of the day variable 5-10 m/s"))
        self.assertEqual(fetch._split_bilingual("საღამის In the evening 8-13"),
                         ("საღამის 8-13", "In the evening 8-13"))

    def test_bilingual_base_and_units(self):
        g, e = fetch._fmt_mta_note(
            "wind_speed_note", "ღამის 2 ნახ.-დან აღმოსავლეთი From the second half of the night Easterly 10-15/17",
            {"wind_speed": "8-13 დროგამოშვებით დაქროლვა Occasional gusts 15-20"})
        self.assertNotIn("Occasional", g)
        self.assertNotIn("დროგამოშვებით", e)
        self.assertTrue(e.startswith("Wind 8-13 Occasional gusts 15-20"))
        self.assertEqual(fetch._split_bilingual("დაქროლვა 12-15 მ/წმ. Gust 12-15 m/sec."),
                         ("დაქროლვა 12-15", "Gust 12-15"))

    def test_plain_notes(self):
        self.assertEqual(fetch._fmt_mta_note("precip_note", "ელჭექი Thunderstorm", {}),
                         ("ელჭექი", "Thunderstorm"))
        self.assertEqual(fetch._split_bilingual("8-13"), ("8-13", "8-13"))


class EastStormTest(unittest.TestCase):
    """2026-10-15: აღმოსავლეთის ძლიერი ქარი ×1.4, ამწის სიმაღლე 1.09, ×1.6 — გაფრთხილება."""
    def _h(self, raw, direction, wind=9.0):
        src = _hours(1, wind_speed=wind, wind_gusts=raw, wind_direction=direction,
                     precipitation=0.0, visibility_km=10.0)
        sea = _hours(1, wave_height=0.4, wave_period=4.0, wave_direction=direction)
        return fetch.compute_consensus(src, None, None, None, sea, None, None, None)[0]

    def test_east_strong_factor(self):
        self.assertEqual(self._h(14.7, 85)["wind_gusts"], round(14.7 * 1.4, 2))
        self.assertEqual(self._h(14.7, 270)["wind_gusts"], round(14.7 * 1.3, 2))
        self.assertEqual(self._h(9.0, 85)["wind_gusts"], round(9.0 * 1.3, 2))   # სუსტი — ×1.3

    def test_crane_height_threshold(self):
        # 15.10 09:07: ნედლი 14.7, E → 20.6 მ/წმ × 1.09 = 22.4 ≥ 21.5 → შეჩერება (რეალობა: შეჩერება)
        self.assertEqual(self._h(14.7, 85)["status"], "suspended")
        # 19.0 მ/წმ × 1.09 = 20.7 < 21.5 → გემები
        self.assertEqual(fetch._compute_status(9, 19.0, 0.2, 10)[0], "vessel")
        self.assertEqual(fetch._compute_status(9, 19.8, 0.2, 10)[0], "suspended")

    def test_crane_risk_warning_only(self):
        # 15.10 07:07: ნედლი 13.8, E → ×1.4 = 19.3 (გემები); ×1.6 = 22.1 → გაფრთხილება
        h = self._h(13.8, 86)
        self.assertEqual(h["status"], "vessel")
        self.assertTrue(h["crane_risk"])
        self.assertTrue(any("შესაძლებელია ამწეების შეჩერება" in a for a in h["alerts"]))
        self.assertFalse(self._h(13.8, 270)["crane_risk"])        # დასავლეთი — არა
        self.assertFalse(self._h(14.7, 85)["crane_risk"])         # უკვე შეჩერებაა — ზედმეტია

    def test_thresholds_exported(self):
        self.assertEqual(fetch.THRESHOLDS["crane_height_factor"], 1.09)


if __name__ == "__main__":
    unittest.main()


def test_forecast_wind_cell_with_gust():
    from mta_parser import _split_wind_gust
    assert _split_wind_gust("10-15\nდაქროლვა\nWith gusts 18-23") == ("10-15", (18.0, 23.0))
    assert _split_wind_gust("8-13\nდროგამოშვებით\nდაქროლვა\nOccasional gusts\n15-18") == ("8-13", (15.0, 18.0))
    assert _split_wind_gust("6-11") == ("6-11", None)


def test_forecast_valid_ignores_footer():
    from mta_parser import forecast_valid
    txt = ("10/15/2026 სსიპ ... ტელ: +995 422 27 49 25/26 ... 15/10/ 2026 წ. 10:00 სთ-დან "
           "Poti Kulevi დღე ღამე 16/10/ 2026 წ. 10:00 სთ-მდე")
    assert forecast_valid(txt) == "15/10/2026 წ. 10:00 სთ-დან 16/10/2026 წ. 10:00 სთ-მდე"
    txt2 = "30/09/ 2026 წ. ფოთი ყულევი 10:00 სთ-დან Poti დღე 01/10/ 2026 წ. Day Night 10:00 სთ-მდე"
    assert forecast_valid(txt2) == "30/09/2026 წ. 10:00 სთ-დან 01/10/2026 წ. 10:00 სთ-მდე"


def test_mta_precip_note():
    from fetch import _fmt_mta_precip
    assert _fmt_mta_precip("ხანმოკლე\nწვიმა,ელჭექი\nBriefly\nrain,Thunderstorm") == (
        "ნალექი: ხანმოკლე წვიმა, ელჭექი", "precipitation: briefly rain, thunderstorm")
    assert _fmt_mta_precip("უნალექოდ\nNo precipitation") is None
    assert _fmt_mta_precip("უმეტესად უნალექოდ\nMainly dry") is None
    assert _fmt_mta_precip(None) is None
