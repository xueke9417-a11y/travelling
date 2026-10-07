"""Deterministic itinerary and money calculation, independent of UI/model."""

import math
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from services import ServiceError, coordinates, weather_for_date


class TripSpec(BaseModel):
    destination: str = Field(min_length=1, max_length=100)
    start_date: date
    end_date: date
    mode: Literal["自驾", "公共交通"]

    @model_validator(mode="after")
    def valid_dates(self):
        if not 1 <= self.days <= 7:
            raise ValueError("请确认日期先后顺序；当前支持 1–7 天行程。")
        return self

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class BudgetParams(BaseModel):
    people: int = Field(default=1, ge=1, le=20)
    food_per_day: Decimal = Field(default=300, ge=0, allow_inf_nan=False)
    hotel_per_night: Decimal = Field(default=300, ge=0, allow_inf_nan=False)
    tickets: Decimal = Field(default=0, ge=0, allow_inf_nan=False)
    fuel_l_per_100km: Decimal = Field(default=8, ge=0, allow_inf_nan=False)
    fuel_price: Decimal = Field(default=Decimal("7.5"), ge=0, allow_inf_nan=False)
    extra_transport: Decimal = Field(default=0, ge=0, allow_inf_nan=False)
    total_limit: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


def _money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_budget(days, mode, distance_m, route_cost, params):
    if days < 1 or mode not in ("自驾", "公共交通"):
        raise ValueError("无效的行程天数或出行方式。")
    transport = None
    if distance_m is not None and route_cost is not None:
        if mode == "自驾":
            fuel = (
                Decimal(str(distance_m))
                / 1000
                / 100
                * params.fuel_l_per_100km
                * params.fuel_price
            )
            transport = _money(fuel + Decimal(str(route_cost)) + params.extra_transport)
        else:
            transport = _money(
                Decimal(str(route_cost)) * params.people + params.extra_transport
            )
    items = {
        "交通": transport,
        "餐饮": _money(params.food_per_day * days),
        "住宿": _money(params.hotel_per_night * (days - 1)),
        "门票": _money(params.tickets),
    }
    known = sum((value for value in items.values() if value is not None), Decimal("0"))
    total = known if transport is not None else None
    return dict(
        items=items,
        total=total,
        known_subtotal=known,
        complete=total is not None,
        remaining=_money(params.total_limit - total)
        if params.total_limit is not None and total is not None
        else None,
    )


def _proximity(a, b):
    lon1, lat1 = coordinates(a["location"])
    lon2, lat2 = coordinates(b["location"])
    return ((lon1 - lon2) * math.cos(math.radians((lat1 + lat2) / 2))) ** 2 + (
        lat1 - lat2
    ) ** 2


def build_itinerary(spec, base, pois, client, progress=None):
    if not pois or len(pois) > min(14, spec.days * 2):
        raise ValueError("请至少选择一个景点，每天最多两个、总计最多十四个。")
    if len({p["id"] for p in pois}) != len(pois):
        raise ValueError("景点不可重复。")
    # Geographic proximity is only a grouping heuristic, not road-route optimality.
    remaining = list(pois)
    days, warnings = [], []
    total_distance, total_cost = 0, Decimal("0")
    distance_complete = cost_complete = True
    deadline = time.monotonic() + 120
    weather_cache = {}
    for index in range(spec.days):
        if progress:
            progress(f"正在查询第 {index + 1}/{spec.days} 天路线与天气")
        current_date = spec.start_date + timedelta(days=index)
        count = math.ceil(len(remaining) / (spec.days - index))
        selected, previous = [], base
        for _ in range(count):
            poi = min(remaining, key=lambda p: _proximity(previous, p))
            remaining.remove(poi)
            selected.append(poi)
            previous = poi
        legs = []
        previous = base
        for stop in selected + ([base] if selected else []):
            leg = dict(
                origin=previous["name"],
                destination=stop["name"],
                source="高德路线",
                available=False,
            )
            try:
                if time.monotonic() > deadline:
                    raise ServiceError("本次查询已超时，请减少景点后重试。")
                leg.update(client.route(previous, stop, spec.mode))
                leg["available"] = True
                total_distance += leg["distance_m"]
                if leg["cost"] is None:
                    cost_complete = False
                else:
                    total_cost += leg["cost"]
            except ServiceError as error:
                leg["error"] = str(error)
                distance_complete = cost_complete = False
            legs.append(leg)
            previous = stop
        area = (selected[0] if selected else base).get("adcode") or base["adcode"]
        if area not in weather_cache:
            try:
                if time.monotonic() > deadline:
                    raise ServiceError("天气查询超时。")
                weather_cache[area] = client.forecast(area)
            except ServiceError as error:
                weather_cache[area] = dict(casts=[])
                warnings.append(str(error))
        weather = weather_for_date(weather_cache[area], current_date.isoformat())
        weather["area"] = (selected[0] if selected else base)["name"]
        duration = sum(leg.get("duration_s", 0) for leg in legs) / 3600
        day_warnings = []
        if duration + len(selected) * 2 > 10:
            day_warnings.append(
                "交通加每景点预留 2 小时已超过 10 小时，建议减少景点或拆分行程。"
            )
        days.append(
            dict(
                date=current_date.isoformat(),
                stops=selected,
                legs=legs,
                weather=weather,
                lodging=base["name"],
                warnings=day_warnings,
            )
        )
    if not distance_complete or not cost_complete:
        warnings.append(
            "部分路线或票价查询失败，交通费和总预算暂不完整；请调整路线后重试。"
        )
    return dict(
        destination=spec.destination,
        mode=spec.mode,
        days=days,
        base=base,
        distance_m=total_distance if distance_complete else None,
        known_distance_m=total_distance,
        route_cost=total_cost if cost_complete else None,
        warnings=list(dict.fromkeys(warnings)),
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        ),
        scope="单个城市/区域、同一住宿起点每日往返；不含抵达目的地的大交通。",
    )
