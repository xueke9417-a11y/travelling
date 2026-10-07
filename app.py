"""Travel MVP: clarification, confirmation, real routes/weather and budget."""

import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from main import CONFIG_PATH, build_request_agent, parse_travel_request
from planner import BudgetParams, TripSpec, build_itinerary, calculate_budget
from services import AmapClient, ServiceError

load_dotenv(CONFIG_PATH)
st.set_page_config(page_title="行程规划助手", page_icon="🧭", layout="wide")
st.title("🧭 行程规划助手")
st.caption("聊需求 → 确认日期与地点 → 选择景点 → 查看逐日路线、天气和预算")
st.info(
    "当前支持 1–7 天、单个城市或区域、同一住宿点每日往返。长线环游需分段规划；不含往返目的地的机票、火车票或订房服务。"
)
for key, value in dict(
    history=[], turns=[], request=None, plan=None, candidates=[], spec=None
).items():
    if key not in st.session_state:
        st.session_state[key] = value


def get_client():
    if "map_client" not in st.session_state:
        st.session_state.map_client = AmapClient(os.getenv("AMAP_API_KEY", ""))
    return st.session_state.map_client


def clear_plan():
    st.session_state.plan = None
    st.session_state.pop("last_budget", None)


with st.sidebar:
    if st.button("重新开始", key="reset"):
        st.session_state.clear()
        st.rerun()
    st.header("预算参数")
    st.caption(
        "金额为人民币。餐饮、住宿、门票均按全组总额填写；人数只用于公交票价。默认值是可修改的假设，不是实时价格。"
    )
    people = st.number_input("同行人数", 1, 20, 1, key="people")
    food = st.number_input(
        "全组每日餐饮（元）", min_value=0.0, value=300.0, step=50.0, key="food"
    )
    hotel = st.number_input(
        "全组每晚住宿（元）", min_value=0.0, value=300.0, step=50.0, key="hotel"
    )
    tickets = st.number_input(
        "全程门票合计（元，需自行填写）",
        min_value=0.0,
        value=0.0,
        step=10.0,
        key="tickets",
    )
    fuel_use = st.number_input(
        "油耗（升/百公里）", min_value=0.0, value=8.0, step=0.5, key="fuel_use"
    )
    fuel_price = st.number_input(
        "油价（元/升）", min_value=0.0, value=7.5, step=0.1, key="fuel_price"
    )
    extra = st.number_input(
        "停车、租车等额外交通费（全程）",
        min_value=0.0,
        value=0.0,
        step=10.0,
        key="extra",
    )
    use_limit = st.checkbox("设置总预算", key="use_limit")
    limit = st.number_input(
        "全组总预算上限（元）",
        min_value=0.0,
        value=3000.0,
        step=100.0,
        key="limit",
        disabled=not use_limit,
    )
budget_params = BudgetParams(
    people=people,
    food_per_day=food,
    hotel_per_night=hotel,
    tickets=tickets,
    fuel_l_per_100km=fuel_use,
    fuel_price=fuel_price,
    extra_transport=extra,
    total_limit=limit if use_limit else None,
)

st.subheader("1. 说说你的旅行想法，也可以直接填下面的表单")
for message in st.session_state.history:
    with st.chat_message(message["role"]):
        st.write(message["content"])
user_input = st.chat_input("例如：我想去成都自驾两天，还没确定具体日期")
if user_input:
    with st.spinner("正在整理需求……"):
        try:
            if "agent" not in st.session_state:
                st.session_state.agent = build_request_agent()
            req = parse_travel_request(
                st.session_state.agent, user_input, st.session_state.turns
            )
            st.session_state.request = req
            answer = req.reply
            if req.destination:
                st.session_state.destination = req.destination
            for field in ("start_date", "end_date"):
                try:
                    st.session_state[field] = date.fromisoformat(getattr(req, field))
                except ValueError:
                    pass
            st.session_state.mode = (
                req.travel_mode if req.travel_mode in ("自驾", "公共交通") else "请选择"
            )
            st.session_state.candidates = []
            st.session_state.pop("selected_pois", None)
            clear_plan()
        except Exception:
            answer = (
                "需求解析暂时失败，请重试或直接填写下方表单；可检查本地模型配置与网络。"
            )
    st.session_state.history.extend(
        [dict(role="user", content=user_input), dict(role="assistant", content=answer)]
    )
    st.session_state.turns.extend([("user", user_input), ("assistant", answer)])
    st.rerun()

st.subheader("2. 确认行程信息并查询真实地点")
today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
if "start_date" not in st.session_state:
    st.session_state.start_date = today + timedelta(days=1)
if "end_date" not in st.session_state:
    st.session_state.end_date = today + timedelta(days=2)
with st.form("trip_form"):
    destination = st.text_input(
        "目的地城市/区域", key="destination", placeholder="例如：成都、伊宁市"
    )
    c1, c2, c3 = st.columns(3)
    start = c1.date_input("开始日期", key="start_date")
    end = c2.date_input("结束日期", key="end_date")
    mode = c3.selectbox("出行方式", ["请选择", "自驾", "公共交通"], key="mode")
    lodging = st.text_input(
        "住宿地点或每日出发点（可留空）",
        key="lodging_query",
        placeholder="填写酒店或具体地点名，查询后需确认地址",
    )
    st.caption(
        "留空会使用城市中心参考点，不代表酒店位置；实际里程可能不同。日期为待确认值，请核对后提交。组合出行请按主要方式分段规划。"
    )
    keywords = st.text_area("想去的景点（每行一个，可留空搜索景区）", key="poi_query")
    search = st.form_submit_button("查询景点与出发点", key="find_places")
if search:
    clear_plan()
    st.session_state.candidates = []
    st.session_state.pop("selected_pois", None)
    st.session_state.pop("selected_base", None)
    try:
        spec = TripSpec(
            destination=destination.strip(), start_date=start, end_date=end, mode=mode
        )
        if start < today:
            raise ValueError("请选择今天或之后的出行日期。")
        queries = list(
            dict.fromkeys(x.strip() for x in keywords.splitlines() if x.strip())
        )
        if len(queries) > 6:
            raise ValueError("每次最多查询六个景点名称，或留空搜索候选景区。")
        with st.spinner("查询高德真实地点……"):
            client = get_client()
            city = client.geocode(spec.destination)
            bases = (
                client.search(spec.destination, lodging.strip())[:5]
                if lodging.strip()
                else [city]
            )
            candidates = []
            for query in queries or [""]:
                candidates.extend(
                    client.search(spec.destination, query, attractions=not bool(query))
                )
            candidates = list({p["id"]: p for p in candidates}.values())
            st.session_state.update(spec=spec, candidates=candidates, bases=bases)
    except (ServiceError, ValueError, ValidationError) as error:
        st.error(
            str(error)
            if not isinstance(error, ValidationError)
            else "请填写目的地、选择出行方式，并确认日期范围为 1–7 天。"
        )

if st.session_state.candidates:
    spec = st.session_state.spec
    st.subheader("3. 确认地点并生成方案")
    st.caption(
        f"当前待生成：{spec.destination} · {spec.start_date} 至 {spec.end_date} · {spec.mode}。修改上方信息后需重新查询。"
    )
    bases = {p["id"]: p for p in st.session_state.bases}
    def label(p):
        return f"{p['name']} · {p.get('address', '')} · {p['location']}"

    base_id = st.selectbox(
        "确认每日住宿/出发点",
        list(bases),
        format_func=lambda key: label(bases[key]),
        key="selected_base",
        on_change=clear_plan,
    )
    pois = {p["id"]: p for p in st.session_state.candidates}
    selected = st.multiselect(
        "确认景点（请核对同名地点与地址）",
        list(pois),
        default=list(pois)[: min(2, spec.days * 2)],
        format_func=lambda key: label(pois[key]),
        key="selected_pois",
        max_selections=min(14, spec.days * 2),
        on_change=clear_plan,
    )
    st.caption(
        "按地理邻近分组，每日最多两个景点并返回出发点；不保证最优路线。开放时间与门票请另行确认。"
    )
    if st.button("生成路线、天气与预算", key="generate", disabled=not selected):
        clear_plan()
        with st.status("正在生成方案……", expanded=True) as status:
            try:
                st.session_state.plan = build_itinerary(
                    spec,
                    bases[base_id],
                    [pois[key] for key in selected],
                    get_client(),
                    progress=lambda text: status.update(label=text),
                )
                status.update(label="方案已生成，请查看数据缺失提示", state="complete")
            except (ServiceError, ValueError):
                status.update(label="生成失败，请调整地点或稍后重试", state="error")

plan = st.session_state.plan
if plan:
    st.subheader("4. 逐日方案")
    st.caption(f"数据查询时间：{plan['generated_at']}。{plan['scope']}")
    for warning in plan["warnings"]:
        st.warning(warning)
    for day in plan["days"]:
        with st.expander(
            f"{day['date']} · "
            + (" → ".join(p["name"] for p in day["stops"]) or "自由活动"),
            expanded=True,
        ):
            st.write(
                f"每日出发/返回：{day['lodging']}。每景点可先预留 2 小时游览（规划假设，需自行确认）。"
            )
            for leg in day["legs"]:
                if leg["available"]:
                    st.write(
                        f"{leg['origin']} → {leg['destination']}：{leg['distance_m'] / 1000:.2f} 公里，约 {leg['duration_s'] / 60:.0f} 分钟（高德查询估计，非未来路况保证）"
                    )
                    if leg["instructions"]:
                        st.caption("换乘参考：" + " → ".join(leg["instructions"]))
                else:
                    st.warning(
                        f"{leg['origin']} → {leg['destination']}：{leg['error']}"
                    )
            weather = day["weather"]
            st.write(f"天气参考区域：{weather['area']} · {weather['text']}")
            if weather["available"]:
                st.write(
                    f"{weather['low']:g}–{weather['high']:g} °C · 预报发布：{weather.get('reporttime', '')}"
                )
            st.caption(weather["advice"] + " 来源：高德天气；区域内山区天气可能不同。")
            for warning in day["warnings"]:
                st.warning(warning)
    st.subheader("预算估算 · 修改左侧参数即时重算")
    budget = calculate_budget(
        len(plan["days"]),
        plan["mode"],
        plan["distance_m"],
        plan["route_cost"],
        budget_params,
    )
    st.session_state.last_budget = budget
    st.table(
        [
            dict(项目=name, 金额="待核实" if value is None else f"¥{value:,.2f}")
            for name, value in budget["items"].items()
        ]
    )
    if budget["complete"]:
        st.metric("预计全组总费用", f"¥{budget['total']:,.2f}")
    else:
        st.warning(
            f"总费用待核实；已知非交通费用小计 ¥{budget['known_subtotal']:,.2f}，不可视为完整预算。"
        )
    if budget["remaining"] is not None:
        if budget["remaining"] < 0:
            st.error(
                f"超支 ¥{-budget['remaining']:,.2f}。可降低住宿或餐饮标准；减少天数/景点需重新查询生成路线。"
            )
        else:
            st.success(f"预算剩余 ¥{budget['remaining']:,.2f}")
    st.caption(
        "自驾交通=高德里程÷1000÷100×油耗×油价+高德过路费+额外交通费；公交交通=高德查询票价×人数+额外交通费。餐饮=全组每日费用×天数；住宿=全组每晚费用×(天数−1)；门票=手动总额。"
    )
    st.caption(
        "门票默认 0 仅代表尚未填写，不代表免费；停车、租车、跨城往返等费用不会自动获取。"
    )
    st.download_button(
        "下载完整方案（JSON）",
        json.dumps(
            dict(plan=plan, budget=budget, budget_params=budget_params.model_dump()),
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        "travel-plan.json",
        "application/json",
    )
