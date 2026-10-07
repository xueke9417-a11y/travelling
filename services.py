"""Amap Web Services boundary. Never expose request URLs or credentials."""

import math
import re
import time
from decimal import Decimal, InvalidOperation

import requests


class ServiceError(RuntimeError):
    pass


def _number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ServiceError("接口未返回有效数字，请稍后重试。") from None


def coordinates(location):
    try:
        lon, lat = map(float, location.split(","))
        if not math.isfinite(lon + lat) or not (
            -180 <= lon <= 180 and -90 <= lat <= 90
        ):
            raise ValueError
        return lon, lat
    except (ValueError, TypeError, AttributeError):
        raise ServiceError("地图未返回有效坐标。") from None


class AmapClient:
    def __init__(self, api_key, session=None):
        if not api_key.strip():
            raise ServiceError("缺少 AMAP_API_KEY，请在本地配置高德 Web 服务密钥。")
        self._key = api_key.strip()
        self._session = session or requests.Session()
        self._cache = {}

    def _get(self, path, **params):
        cache_key = (path, tuple(sorted(params.items())))
        cached = self._cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        try:
            response = self._session.get(
                "https://restapi.amap.com/v3/" + path,
                params={**params, "key": self._key},
                timeout=(3, 6),
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            raise ServiceError("高德服务连接失败或响应异常，请稍后重试。") from None
        if not isinstance(data, dict) or str(data.get("status")) != "1":
            code = str(data.get("infocode", "")) if isinstance(data, dict) else ""
            code = code if re.fullmatch(r"\d{5}", code) else "未知"
            raise ServiceError(
                f"高德接口未成功（错误码 {code}），请检查权限、额度或稍后重试。"
            )
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[cache_key] = (time.monotonic(), data)
        return data

    def search(self, city, keywords="", attractions=False):
        params = dict(
            city=city,
            citylimit="true",
            keywords=keywords,
            offset=20,
            page=1,
            extensions="all",
        )
        if attractions:
            params["types"] = "110000"
        data = self._get("place/text", **params)
        result = []
        for poi in data.get("pois") or []:
            location = poi.get("location", "")
            try:
                coordinates(location)
            except ServiceError:
                continue
            if not poi.get("id") or not poi.get("name"):
                continue
            result.append(
                dict(
                    id=poi["id"],
                    name=poi["name"],
                    location=location,
                    city=poi.get("cityname") or city,
                    adcode=poi.get("adcode") or poi.get("adname") or city,
                    address=poi.get("address")
                    if isinstance(poi.get("address"), str)
                    else "",
                    source="高德 POI",
                )
            )
        if not result:
            raise ServiceError("该城市内未找到地点，请使用具体城市和更完整的地点名。")
        return result

    def geocode(self, city):
        data = self._get("geocode/geo", address=city)
        values = data.get("geocodes") or []
        if not values:
            raise ServiceError("未找到目的地，请填写具体城市。")
        value = values[0]
        coordinates(value.get("location"))
        return dict(
            id="city-center",
            name=f"{city}中心参考点",
            location=value["location"],
            city=value.get("city") or city,
            adcode=value.get("adcode") or city,
            address=value.get("formatted_address", ""),
            source="高德地理编码",
        )

    def route(self, origin, destination, mode):
        if mode not in ("自驾", "公共交通"):
            raise ServiceError("请明确选择自驾或公共交通。")
        if origin["location"] == destination["location"]:
            return dict(distance_m=0, duration_s=0, cost=Decimal("0"), instructions=[])
        params = dict(
            origin=origin["location"],
            destination=destination["location"],
            extensions="base",
        )
        if mode == "自驾":
            data = self._get("direction/driving", **params)
            paths = (data.get("route") or {}).get("paths") or []
        else:
            data = self._get(
                "direction/transit/integrated",
                **params,
                city=origin["city"],
                cityd=destination["city"],
                strategy=0,
            )
            paths = (data.get("route") or {}).get("transits") or []
        if not paths:
            raise ServiceError("未查到该段路线，请调整景点或出行方式。")
        path = paths[0]
        distance = int(_number(path.get("distance")))
        duration = int(_number(path.get("duration")))
        cost_value = path.get("tolls" if mode == "自驾" else "cost")
        try:
            cost = _number(cost_value)
        except ServiceError:
            cost = None
        instructions = []
        if mode == "公共交通":
            for segment in path.get("segments") or []:
                for line in (segment.get("bus") or {}).get("buslines") or []:
                    if line.get("name"):
                        instructions.append(line["name"])
        return dict(
            distance_m=distance,
            duration_s=duration,
            cost=cost,
            instructions=instructions,
        )

    def forecast(self, adcode):
        data = self._get("weather/weatherInfo", city=adcode, extensions="all")
        forecasts = data.get("forecasts") or []
        if not forecasts:
            raise ServiceError("暂未获取到该地区天气预报。")
        return forecasts[0]


def weather_for_date(forecast, travel_date):
    for cast in forecast.get("casts") or []:
        if cast.get("date") != str(travel_date):
            continue
        try:
            high, low = float(cast["daytemp"]), float(cast["nighttemp"])
            if not math.isfinite(high + low):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            break
        phenomenon = f"白天{cast.get('dayweather', '未知')} / 夜间{cast.get('nightweather', '未知')}"
        advice = "早晚温差请留意，按实际体感增减衣物。"
        if any(word in phenomenon for word in ("雨", "雪", "雷")):
            advice = "雨雪天气提醒：携带雨具和防滑鞋；减少户外活动，出发前核查开放情况并考虑室内替代。"
        elif max(high, low) >= 30:
            advice = "注意防晒补水，避开午间高温。"
        elif min(high, low) <= 10:
            advice = "气温较低，携带保暖外套。"
        return dict(
            available=True,
            text=phenomenon,
            high=max(high, low),
            low=min(high, low),
            advice=advice,
            source="高德天气",
            reporttime=forecast.get("reporttime", ""),
        )
    return dict(
        available=False,
        text="该日期暂无预报",
        advice="出发前再次查看天气，当前不推测温度或降雨。",
        source="高德天气",
    )
