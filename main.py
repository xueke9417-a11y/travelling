"""
行程规划 Agent —— 行前澄清模块（完整方案在 app.py 页面生成）
PRD：portfolio/prd.md（P0-1）；技术选型：portfolio/competitive_analysis.md（方案 A）

P0-1 做什么：从用户消息解析行前三要素——目的地、出行日期、出行方式。
- 目的地是省/大区域（如"新疆"）→ 追问具体城市，不默认假定
- 出行方式未告知 → 先问（自驾/公共交通/组合式），不猜（用户设计的交互）
- 日期模糊（"十一""下个月"）→ 结合今天日期换算确认
三要素齐备 → ready=True，进入页面确认和景点选择

设计哲学：模型负责解析与判断，程序负责校验——模型只被允许输出 JSON，
Pydantic schema 兜底，解析不出来的字段留空，禁止编造。
"""

import json
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, model_validator

# 独立部署时读取本仓库根目录 .env；找不到则回退到本地开发环境的 simple_ai_agents/.env
CONFIG_PATH = Path(__file__).resolve().parent / ".env"
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path(__file__).resolve().parents[2] / "simple_ai_agents" / ".env"


class TravelRequest(BaseModel):
    """P0-1 的结构化产出：三要素 + 给用户看的回复。空字符串 = 尚未确认。"""

    reply: str = Field(
        ..., description="给用户看的中文回复：要么追问缺失项，要么复述确认"
    )
    ready: bool = Field(..., description="三要素全部具体明确时为 true")
    destination: str = Field(
        "", description="具体目的地城市/区域，如 乌鲁木齐、伊犁；不明确则留空"
    )
    start_date: str = Field("", description="出行开始日期 YYYY-MM-DD，不明确则留空")
    end_date: str = Field("", description="出行结束日期 YYYY-MM-DD，不明确则留空")
    travel_mode: str = Field(
        "", description="自驾 / 公共交通 / 组合式 之一，不明确则留空"
    )

    @model_validator(mode="after")
    def validate_ready(self):
        try:
            start, end = (
                date.fromisoformat(self.start_date),
                date.fromisoformat(self.end_date),
            )
            valid = (
                bool(self.destination.strip())
                and end >= start
                and self.travel_mode in ("自驾", "公共交通", "组合式")
            )
        except ValueError:
            valid = False
        self.ready = self.ready and valid
        if not valid and self.ready is False:
            self.reply += " 请核对目的地、有效日期和出行方式。"
        return self


INSTRUCTIONS = [
    "你是行程规划助手的'行前澄清'模块。任务：从用户消息中识别行前三要素——"
    "目的地（具体到城市/区域）、出行日期（开始到结束）、出行方式（自驾/公共交通/组合式）。",
    "每轮只追问缺失项中最关键的一个问题，不要一次问一串；已经明确的要素要在回复里顺带复述，让用户感到被听见。",
    "目的地是省或大区域（如'新疆''云南''川西'）时，必须追问具体城市或区域，禁止自行假定；"
    "市/县一级（如'伊犁''乌鲁木齐'）才算明确。",
    "日期模糊（'十一''下个月''周末'）时，结合今天日期换算成具体区间并与用户确认；换算不了就追问。",
    "出行方式用户没说时必须先问，禁止默认为任何一种。",
    "三要素都具体明确时，ready=true，reply 用不超过 60 字复述确认，并提示用户在下方表单确认和选择景点；禁止声称已经生成路线或预算。",
    "只有上旬、中旬、下旬等模糊日期时，start_date 和 end_date 留空并追问，禁止擅自挑选具体日期。",
    "禁止编造：解析不出来的字段一律留空字符串，不许猜。",
    "只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释文字。"
    "字段：reply, ready, destination, start_date, end_date, travel_mode。",
]


def build_request_agent():
    """P0-1 澄清助手。模型配置与 daily_push.py 同源（DeepSeek + 关闭思考）。"""
    from agno.agent import Agent
    from agno.models.openai import OpenAIChat

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ValueError("请在 simple_ai_agents/.env 中填写 DEEPSEEK_API_KEY。")
    return Agent(
        name="Travel Request Clarifier",
        instructions=INSTRUCTIONS,
        model=OpenAIChat(
            id=os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-flash",
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
            role_map={
                "system": "system",
                "user": "user",
                "assistant": "assistant",
                "tool": "tool",
            },
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=800,
        ),
        markdown=False,
        telemetry=False,
    )


def load_and_build():
    load_dotenv(CONFIG_PATH)
    return build_request_agent()


def parse_travel_request(agent, user_input, history=None):
    """模型只输出 JSON，程序拼上下文并做结构校验。history=[("user"/"assistant", 文本), ...]

    多轮上下文由程序手工拼接（不依赖框架会话机制），保证澄清对话可测试、可复现。
    """
    turns = history or []
    lines = [f"[今天日期：{date.today().isoformat()}]"]
    if turns:
        lines.append("[之前的对话]")
        for role, text in turns[-6:]:
            lines.append(f"{'用户' if role == 'user' else '助手'}: {text}")
    lines.append("[当前消息]")
    lines.append(f"用户: {user_input}")

    result = agent.run("\n".join(lines))
    text = (result.content or "").strip()
    if text.startswith("```"):  # 兜底剥掉可能的代码围栏
        text = text.strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("模型未返回有效 JSON，请重试。")
    return TravelRequest(**json.loads(text[start : end + 1]))


def main():
    try:
        agent = load_and_build()
    except ValueError as error:
        print(error)
        return
    print("行程规划助手 · 行前澄清（P0-1）已启动")
    print("说出你的旅行想法，例如：我想 10 月下旬去新疆玩 6 天。输入 exit 退出。")

    turns = []
    while True:
        user_input = input("\n你: ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "退出"):
            print("再见！")
            break
        try:
            req = parse_travel_request(agent, user_input, turns)
        except (RuntimeError, ValidationError) as error:
            print(f"助手: （解析出错，请换个说法重试）{error}")
            continue
        print(f"助手: {req.reply}")
        turns.append(("user", user_input))
        turns.append(("assistant", req.reply))
        if req.ready:
            print(
                f"   [三要素齐备] 目的地={req.destination} | "
                f"日期={req.start_date}~{req.end_date} | 方式={req.travel_mode}"
            )
            print("   请打开 Streamlit 页面，确认景点后生成路线、天气和预算。")


if __name__ == "__main__":
    main()
