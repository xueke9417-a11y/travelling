"""
第一轮开发自检脚本（P0-1 行前澄清 + 高德 Key 验证）

在本仓库根目录用 PowerShell 运行：
  .\\.venv\\Scripts\\python.exe check_round1.py

脚本自定位路径，不依赖当前目录；只打印 Key 的形状（长度/字符集），不打印 Key 内容。
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent  # 仓库根目录
sys.path.insert(0, str(BASE))
os.chdir(BASE)

from dotenv import load_dotenv

load_dotenv(BASE / ".env")

import requests

print("=== 1. 高德 Key 验证 ===")
amap_key = (os.getenv("AMAP_API_KEY") or "").strip()
print("Key 形状: 长度=", len(amap_key), "| 纯ASCII=", amap_key.isascii())
resp = requests.get(
    "https://restapi.amap.com/v3/geocode/geo",
    params={"address": "乌鲁木齐", "key": amap_key},
    timeout=15,
)
result = resp.json()
print("AMAP geocode:", result.get("status"), "|", result.get("info"))
if result.get("status") == "1" and result.get("geocodes"):
    geo = result["geocodes"][0]
    print("样例结果:", geo.get("city"), geo.get("location"))

print("\n=== 2. P0-1 行前澄清（3 条验收场景）===")
from main import load_and_build, parse_travel_request

agent = load_and_build()
history = []

# 场景 1：省级目的地 → 必须追问具体城市，不默认假定
r1 = parse_travel_request(agent, "我想10月下旬去新疆玩6天", history)
print("R1 ready=", r1.ready, "| reply=", r1.reply)
print("   dest=", repr(r1.destination), "| mode=", repr(r1.travel_mode))
history += [("user", "我想10月下旬去新疆玩6天"), ("assistant", r1.reply)]

# 场景 2：补齐方式与城市 → ready=True 且三要素解析正确
r2 = parse_travel_request(agent, "自驾，具体去伊犁，10月24日到29日", history)
print("R2 ready=", r2.ready, "| reply=", r2.reply)
print(
    "   dest=", repr(r2.destination), "| mode=", repr(r2.travel_mode),
    "| dates=", repr(r2.start_date), repr(r2.end_date),
)

# 场景 3：城市和日期都明确、但没说出行方式 → 必须先问方式
r3 = parse_travel_request(agent, "我10月24日到29日想去伊犁玩", [])
print("R3 ready=", r3.ready, "| reply=", r3.reply)
print("   dest=", repr(r3.destination), "| mode=", repr(r3.travel_mode))
