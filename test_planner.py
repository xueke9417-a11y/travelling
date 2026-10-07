import unittest
from decimal import Decimal
from unittest.mock import Mock

import main

try:
    import planner
    import services
except ImportError:
    planner = services = None


class ValidationTests(unittest.TestCase):
    def test_ready_cannot_bypass_missing_or_invalid_fields(self):
        for data in [
            dict(),
            dict(
                destination="成都",
                start_date="2026-02-30",
                end_date="2026-03-01",
                travel_mode="自驾",
            ),
            dict(
                destination="成都",
                start_date="2026-10-10",
                end_date="2026-10-09",
                travel_mode="自驾",
            ),
        ]:
            with self.subTest(data=data):
                req = main.TravelRequest(reply="ok", ready=True, **data)
                self.assertFalse(req.ready)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(planner, "planner workflow is not implemented")
        self.assertIsNotNone(services, "map/weather services are not implemented")

    def test_budget_uses_nights_and_litres_per_100km(self):
        result = planner.calculate_budget(
            days=3,
            mode="自驾",
            distance_m=100000,
            route_cost=Decimal("20"),
            params=planner.BudgetParams(
                food_per_day=300,
                hotel_per_night=200,
                tickets=100,
                fuel_l_per_100km=8,
                fuel_price=7.5,
                total_limit=1500,
            ),
        )
        self.assertEqual(result["total"], Decimal("1480.00"))
        self.assertEqual(result["remaining"], Decimal("20.00"))
        self.assertEqual(result["items"]["交通"], Decimal("80.00"))

    def test_unknown_route_never_becomes_complete_zero_cost_budget(self):
        result = planner.calculate_budget(
            days=2,
            mode="自驾",
            distance_m=None,
            route_cost=None,
            params=planner.BudgetParams(),
        )
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total"])
        self.assertIsNone(result["remaining"])

    def test_transit_fares_multiply_people_and_negative_inputs_rejected(self):
        result = planner.calculate_budget(
            days=1,
            mode="公共交通",
            distance_m=10000,
            route_cost=Decimal("6"),
            params=planner.BudgetParams(
                people=2, food_per_day=0, hotel_per_night=0, tickets=0, total_limit=10
            ),
        )
        self.assertEqual(result["total"], Decimal("12.00"))
        self.assertEqual(result["remaining"], Decimal("-2.00"))
        with self.assertRaises(ValueError):
            planner.BudgetParams(fuel_price=-1)

    def test_request_rejects_reversed_dates_and_mixed_mode(self):
        for start, end, mode in [
            ("2026-10-10", "2026-10-09", "自驾"),
            ("2026-10-10", "2026-10-11", "组合式"),
        ]:
            with self.assertRaises(ValueError):
                planner.TripSpec(
                    destination="成都", start_date=start, end_date=end, mode=mode
                )

    def test_itinerary_includes_return_legs_and_weather_degradation(self):
        client = Mock()
        client.route.return_value = dict(
            distance_m=1000,
            duration_s=120,
            cost=Decimal("0"),
            instructions=["步行或驾车路线"],
        )
        client.forecast.side_effect = services.ServiceError("天气暂不可用")
        base = dict(
            id="base", name="住宿起点", location="104,30", adcode="510100", city="成都"
        )
        pois = [
            dict(
                id=str(i),
                name=f"景点{i}",
                location=f"104.0{i + 1},30",
                adcode="510100",
                city="成都",
            )
            for i in range(2)
        ]
        result = planner.build_itinerary(
            planner.TripSpec(
                destination="成都",
                start_date="2026-10-08",
                end_date="2026-10-09",
                mode="自驾",
            ),
            base,
            pois,
            client,
        )
        self.assertEqual(len(result["days"]), 2)
        self.assertEqual(result["distance_m"], 4000)
        self.assertTrue(all(len(d["legs"]) == 2 for d in result["days"]))
        self.assertTrue(all(d["weather"]["available"] is False for d in result["days"]))
        self.assertTrue(result["warnings"])

    def test_failed_leg_keeps_partial_results_and_incomplete_totals(self):
        client = Mock()
        client.route.side_effect = services.ServiceError("路线不可用")
        client.forecast.return_value = dict(casts=[], reporttime="")
        base = dict(
            id="base", name="起点", location="104,30", adcode="510100", city="成都"
        )
        poi = dict(
            id="one", name="景点", location="104.1,30", adcode="510100", city="成都"
        )
        result = planner.build_itinerary(
            planner.TripSpec(
                destination="成都",
                start_date="2026-10-08",
                end_date="2026-10-08",
                mode="自驾",
            ),
            base,
            [poi],
            client,
        )
        self.assertIsNone(result["distance_m"])
        self.assertIsNone(result["route_cost"])
        self.assertFalse(result["days"][0]["legs"][0]["available"])

    def test_weather_matches_date_and_flags_rain(self):
        weather = services.weather_for_date(
            dict(
                casts=[
                    dict(
                        date="2026-10-08",
                        dayweather="小雨",
                        nightweather="阴",
                        daytemp="23",
                        nighttemp="17",
                    )
                ],
                reporttime="2026-10-07",
            ),
            "2026-10-08",
        )
        self.assertTrue(weather["available"])
        self.assertIn("雨", weather["advice"])
        self.assertEqual(weather["low"], 17)
        self.assertFalse(
            services.weather_for_date(dict(casts=[]), "2026-12-01")["available"]
        )

    def test_http_errors_hide_keys_and_api_empty_routes_stay_unknown(self):
        session = Mock()
        import requests

        session.get.side_effect = requests.RequestException(
            "https://example/?key=SECRET"
        )
        client = services.AmapClient("SECRET", session=session)
        with self.assertRaises(services.ServiceError) as error:
            client.search("成都", "草堂")
        self.assertNotIn("SECRET", str(error.exception))
        session.get.side_effect = None
        session.get.return_value.json.return_value = dict(
            status="1", route=dict(paths=[])
        )
        a = dict(location="104,30", city="成都")
        b = dict(location="104.1,30", city="成都")
        with self.assertRaises(services.ServiceError):
            client.route(a, b, "自驾")

    def test_api_units_and_transit_distance_are_from_selected_path(self):
        session = Mock()
        session.get.return_value.json.return_value = dict(
            status="1",
            route=dict(
                distance="999999",
                transits=[dict(distance="1200", duration="600", cost="3", segments=[])],
            ),
        )
        client = services.AmapClient("test", session=session)
        route = client.route(
            dict(location="104,30", city="成都"),
            dict(location="104.1,30", city="成都"),
            "公共交通",
        )
        self.assertEqual(route["distance_m"], 1200)
        self.assertEqual(route["duration_s"], 600)
        self.assertEqual(route["cost"], Decimal("3"))


if __name__ == "__main__":
    unittest.main()
