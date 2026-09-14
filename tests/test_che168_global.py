"""
Tests for the global.che168.com client.

Fixtures in tests/fixtures/che168_global are real API responses saved on
2026-09-14. Run from the repo root:

    venv/bin/python -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import che168_scraper as che  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "che168_global"
RATE = 6.575


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_car(infoid, specid):
    carinfo = load_json(f"carinfo_{infoid}.json")["result"]
    spec = load_json(f"specparam_{specid}.json")["result"]
    return carinfo, spec


def fake_response(status=200, content_type="application/json; charset=utf-8", body=""):
    response = mock.Mock()
    response.status_code = status
    response.headers = {"Content-Type": content_type, "Server": "TencentEdgeOne"}
    response.text = body
    response.json.side_effect = lambda: json.loads(body)
    return response


class UrlTests(unittest.TestCase):
    def test_global_links(self):
        cases = {
            "https://global.che168.com/en/detail/59826201": "59826201",
            "https://global.che168.com/ru/detail/59826201?session_id=abc&fromsource=0": "59826201",
            "https://global.che168.com/detail/59892752?session_id=abc": "59892752",
            "Смотри вот эту: https://global.che168.com/en/detail/59826201 норм?": "59826201",
            "HTTPS://GLOBAL.CHE168.COM/EN/DETAIL/59826201": "59826201",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(che.extract_global_car_id(text), expected)
                self.assertTrue(che.is_che168_global_url(text))

    def test_non_global_links(self):
        for text in (
            "https://m.che168.com/cardetail/index?infoid=59826201",
            "https://global.che168.com/en/used-cars",
            "https://global.che168.com/en/detail/1",
            "https://fem.encar.com/cars/detail/12345678",
            "",
            None,
        ):
            with self.subTest(text=text):
                self.assertIsNone(che.extract_global_car_id(text))

    def test_legacy_links(self):
        cases = {
            "https://m.che168.com/cardetail/index?infoid=59826201&pvareaid=108721&cartype=70": "59826201",
            # A real shared link, tracking params included
            "https://m.che168.com/cardetail/index?infoid=59826201&pvareaid=108721&cpcid=0&isrecom=0"
            "&queryid=1789300366567$0$BA09F697-B39E-47E2-B3BF-A30E883AB273$16477$1&cartype=70"
            "&cxextraparamsnew=&offertype=10007&offertag=0&activitycartype=0&cstencryptinfo=&encryptinfo="
            "&userareaid=0&adfromid=0&fromtag=0&ext=%7B%22urltype%22%3A%22%22%7D"
            "&otherstatisticsext=%7B%22abtest0923%22%3A%22%22%2C%22carrange%22%3A1%2C%22cartype%22%3A70"
            "%2C%22pvareaid%22%3A%22108721%22%2C%22srecom%22%3A%221%22%7D": "59826201",
            "Посчитайте https://m.che168.com/cardetail/index?infoid=59826201&pvareaid=108721": "59826201",
            "https://m.che168.com/dealer/657408/56913158.html": "56913158",
            "https://www.che168.com/usedcar/56913158.html": "56913158",
            "https://i.che168.com/car/56913158": "56913158",
            "https://global.che168.com/en/detail/59826201": None,
            "https://www.che168.com/": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(che.extract_legacy_car_id(text), expected)


class EnergyTypeTests(unittest.TestCase):
    def test_mapping(self):
        cases = {
            "Gasoline": 1,
            "Gasoline + 48V Mild Hybrid System": 1,
            "Diesel + 48V Mild Hybrid System": 2,
            "Diesel": 2,
            "Plug-in Hybrid": 6,
            "Gasoline-Electric Hybrid": 6,
            "Pure Electric": 4,
            "Range Extender": 5,
            "Extended-Range Electric": 5,
            "--": None,
            "": None,
            None: None,
        }
        for fuel_name, expected in cases.items():
            with self.subTest(fuel_name=fuel_name):
                self.assertEqual(che.map_energy_type(fuel_name)[0], expected)


class PowerTests(unittest.TestCase):
    def power(self, infoid, specid):
        carinfo, spec = load_car(infoid, specid)
        code, _ = che.map_energy_type(carinfo["fuelname"])
        return che.select_power_hp(code, che._spec_values(spec), carinfo["engine"])

    def test_real_listings(self):
        self.assertEqual(self.power(59826201, 55683), 170)   # petrol + 48V: engine Ps
        self.assertEqual(self.power(59842403, 64906), 313)   # plug-in hybrid: combined Ps
        self.assertEqual(self.power(59894021, 75116), 264)   # EV: motor Ps
        self.assertEqual(self.power(59182671, 72005), 272)   # range extender: motor Ps

    def test_fallbacks(self):
        self.assertEqual(che.select_power_hp(1, {che.SPEC_ENGINE_KW: "125"}, ""), 170)
        self.assertEqual(che.select_power_hp(1, {}, "2.0T 245HP L4"), 245)
        self.assertEqual(che.select_power_hp(4, {che.SPEC_MOTOR_KW: "194"}, "--"), 264)
        self.assertEqual(che.select_power_hp(6, {che.SPEC_COMBINED_KW: "230"}, ""), 313)
        self.assertEqual(
            che.select_power_hp(6, {che.SPEC_ENGINE_PS: "204", che.SPEC_MOTOR_PS: "129"}, ""), 333
        )

    def test_unknown_power_is_none(self):
        self.assertIsNone(che.select_power_hp(1, {}, "--"))
        self.assertIsNone(che.select_power_hp(4, {}, "--"))
        self.assertIsNone(che.select_power_hp(6, {che.SPEC_ENGINE_PS: "204"}, ""))


class PriceTests(unittest.TestCase):
    # (USD on global.che168.com, CNY on m.che168.com) checked on 2026-09-14
    VERIFIED = [
        (15510, 102000), (16090, 105800), (23270, 153000),
        (24910, 163800), (15330, 100800), (28870, 189800),
        (19740, 129800), (17770, 116800), (21720, 142800),
    ]

    def test_verified_pairs(self):
        for usd, cny in self.VERIFIED:
            with self.subTest(usd=usd):
                self.assertEqual(che.usd_to_cny(usd, RATE), cny)

    def test_rate_drift_warning(self):
        # 15510 x 7.05 = 109345.5, 45.5 yuan away from a round hundred
        with self.assertLogs(level="WARNING"):
            che.usd_to_cny(15510, 7.05)


class ParseTests(unittest.TestCase):
    def test_petrol_mild_hybrid(self):
        car = che.parse_global_car(*load_car(59826201, 55683), RATE)
        self.assertEqual(car["price_usd"], 15510)
        self.assertEqual(car["price_cny"], 102000)
        self.assertEqual(car["displacement_cc"], 1496)
        self.assertEqual(car["horsepower"], 170)
        self.assertEqual(car["fuel_type_code"], 1)
        self.assertEqual((car["age_year"], car["age_month"], car["age_source"]), (2022, 1, "manufacture"))
        self.assertEqual(car["mileage_km"], 70000)
        self.assertEqual(car["car_name"], "Mercedes-Benz C-Class 2022 Facelift C 200 L Sport")
        self.assertEqual(car["city_name"], "Shanghai")
        self.assertEqual(len(car["photos"]), 8)
        self.assertEqual(car["link"], "https://global.che168.com/ru/detail/59826201")

    def test_ev_has_zero_displacement(self):
        car = che.parse_global_car(*load_car(59894021, 75116), RATE)
        self.assertEqual(car["fuel_type_code"], 4)
        self.assertEqual(car["displacement_cc"], 0)
        self.assertEqual((car["age_year"], car["age_month"], car["age_source"]), (2026, 6, "registration"))

    def test_plug_in_hybrid_falls_back_to_regdate(self):
        car = che.parse_global_car(*load_car(59842403, 64906), RATE)
        self.assertEqual(car["fuel_type_code"], 6)
        self.assertEqual(car["displacement_cc"], 1999)
        self.assertEqual((car["age_year"], car["age_month"], car["age_source"]), (2023, 12, "registration"))

    def test_missing_price_raises(self):
        carinfo, spec = load_car(59826201, 55683)
        for price in ("", "0", "--", None):
            with self.subTest(price=price), self.assertRaises(che.Che168DataError):
                che.parse_global_car({**carinfo, "price": price}, spec, RATE)

    def test_missing_date_raises(self):
        carinfo, spec = load_car(59826201, 55683)
        broken = {**carinfo, "manufacturedate": "", "regdate": "2022.13", "producedate": ""}
        with self.assertRaises(che.Che168DataError):
            che.parse_global_car(broken, spec, RATE)

    def test_missing_displacement_for_petrol_raises(self):
        carinfo, _ = load_car(59826201, 55683)
        with self.assertRaises(che.Che168DataError):
            che.parse_global_car({**carinfo, "engine": "--"}, {}, RATE)

    def test_unknown_fuel_defers_to_user(self):
        carinfo, spec = load_car(59826201, 55683)
        car = che.parse_global_car({**carinfo, "fuelname": "--"}, spec, RATE)
        self.assertIsNone(car["fuel_type_code"])
        self.assertIsNone(car["horsepower"])

    def test_spec_values_use_sublist_and_first_value(self):
        spec = che._spec_values(load_json("specparam_55683.json")["result"])
        self.assertEqual(spec[86], "Rear-Wheel Drive (RWD)")
        self.assertEqual(spec[24], "Sedan")
        self.assertEqual(che._num("150/218"), 150.0)
        self.assertIsNone(che._num("--"))


class ApiGetTests(unittest.TestCase):
    def session_returning(self, response):
        session = mock.Mock()
        session.get.return_value = response
        return session

    def test_ok(self):
        body = (FIXTURES / "carinfo_59826201.json").read_text(encoding="utf-8")
        result = che._api_get(self.session_returning(fake_response(body=body)), "carinfo/59826201")
        self.assertEqual(result["infoid"], 59826201)

    def test_edgeone_page_is_blocked(self):
        body = (FIXTURES / "edgeone_challenge.html").read_text(encoding="utf-8")
        response = fake_response(content_type="text/html", body=body)
        with self.assertLogs(level="WARNING"), self.assertRaises(che.Che168Blocked):
            che._api_get(self.session_returning(response), "carinfo/59826201")

    def test_null_result_is_not_found(self):
        body = (FIXTURES / "notfound.json").read_text(encoding="utf-8")
        session = self.session_returning(fake_response(body=body))
        with self.assertRaises(che.Che168NotFound):
            with mock.patch.object(che, "_create_session", return_value=session):
                che.get_global_car_info("59826202")

    def test_invalid_id_is_not_found(self):
        body = (FIXTURES / "invalid_id.json").read_text(encoding="utf-8")
        with self.assertRaises(che.Che168NotFound):
            che._api_get(self.session_returning(fake_response(body=body)), "carinfo/abc")

    def test_network_error_is_unavailable(self):
        session = mock.Mock()
        session.get.side_effect = che.requests.ConnectionError("boom")
        with self.assertRaises(che.Che168Unavailable):
            che._api_get(session, "carinfo/59826201")

    def test_specs_failure_is_not_fatal(self):
        carinfo_body = (FIXTURES / "carinfo_59826201.json").read_text(encoding="utf-8")
        session = mock.Mock()
        session.get.side_effect = [
            fake_response(body=carinfo_body),
            che.requests.ConnectionError("specs down"),
        ]
        with mock.patch.object(che, "_create_session", return_value=session), self.assertLogs(level="WARNING"):
            car = che.get_global_car_info("59826201")
        # Without specs, power and displacement come from the engine string "1.5T 170hp L4"
        self.assertEqual(car["horsepower"], 170)
        self.assertEqual(car["displacement_cc"], 1500)


class FormattingTests(unittest.TestCase):
    def test_mileage(self):
        self.assertEqual(che.format_mileage(70000), "70 тыс. км")
        self.assertEqual(che.format_mileage(70688), "70.7 тыс. км")
        self.assertEqual(che.format_mileage(800), "800 км")

    def test_gearbox(self):
        cases = {
            "9-speed automatic transmission": "Автомат",
            "7-speed dual-clutch transmission": "Робот (DCT)",
            "CVT continuously variable transmission": "Вариатор",
            "Electric vehicle single-speed transmission": "Редуктор",
            "6-speed manual transmission": "Механика",
        }
        for gearbox, expected in cases.items():
            with self.subTest(gearbox=gearbox):
                self.assertEqual(che.format_gearbox(gearbox), expected)


if __name__ == "__main__":
    unittest.main()
