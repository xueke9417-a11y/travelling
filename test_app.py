"""UI workflow tests: real Streamlit UI + deterministic external HTTP fixtures."""

import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def api_fixture(self, path, **params):
    if path == "geocode/geo":
        return dict(geocodes=[dict(location="104,30", city="成都", adcode="510100")])
    if path == "place/text":
        return dict(
            pois=[
                dict(
                    id="a",
                    name="甲景点",
                    location="104.01,30",
                    cityname="成都",
                    adcode="510100",
                ),
                dict(
                    id="b",
                    name="乙景点",
                    location="104.02,30",
                    cityname="成都",
                    adcode="510100",
                ),
            ]
        )
    if path == "weather/weatherInfo":
        return dict(forecasts=[dict(casts=[])])
    if path == "direction/driving":
        return dict(
            route=dict(paths=[dict(distance="1000", duration="300", tolls="0")])
        )
    raise AssertionError(f"Unexpected external path: {path}")


class AppTests(unittest.TestCase):
    def test_confirm_select_generate_and_recalculate_without_network(self):
        with (
            patch.dict("os.environ", {"AMAP_API_KEY": "test"}),
            patch("services.AmapClient._get", api_fixture),
        ):
            app = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(
                any(x.key == "destination" for x in app.text_input),
                "missing confirmation form",
            )
            app.text_input(key="destination").set_value("成都")
            app.date_input(key="start_date").set_value(date.today() + timedelta(days=1))
            app.date_input(key="end_date").set_value(date.today() + timedelta(days=1))
            app.selectbox(key="mode").select("自驾")
            app.button(key="find_places").click().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.session_state["candidates"]), 2)
            app.button(key="generate").click().run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            plan = app.session_state["plan"]
            self.assertEqual(plan["distance_m"], 3000)
            self.assertEqual(len(plan["days"][0]["legs"]), 3)
            first_total = app.session_state["last_budget"]["total"]
            app.number_input(key="food").set_value(400.0).run()
            self.assertEqual(
                app.session_state["last_budget"]["total"] - first_total, 100
            )
            app.multiselect(key="selected_pois").set_value(["a"]).run()
            self.assertIsNone(app.session_state["plan"])
            app.button(key="generate").click().run(timeout=20)
            self.assertEqual(app.session_state["plan"]["distance_m"], 2000)
            app.button(key="reset").click().run()
            self.assertIsNone(app.session_state["plan"])


if __name__ == "__main__":
    unittest.main()
