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
import mta_parser     # noqa: E402


def _hours(n, start=0, **fields):
    return [{"time": f"2026-10-01T{start + i:02d}:00", **fields} for i in range(n)]


class ComputeStatusTest(unittest.TestCase):
    def test_ladder(self):
        T = fetch.THRESHOLDS
        self.assertEqual(fetch._compute_status(1, 2, 0.2, 10)[0], "operational")
        self.assertEqual(fetch._compute_status(5, T["wind_barge"], 0.2, 10)[0], "barge")
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
        self.assertEqual(out[0]["wind_speed"], 10.0)
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


if __name__ == "__main__":
    unittest.main()
