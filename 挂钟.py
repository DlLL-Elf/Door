#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
门 · 挂钟（会敲门的 MCP 客户端）
================================

这是家的自动门。它不干别的，就做一件事：
到点了，替人类按一下门铃，说一句「欢迎回家，AI」，
然后把家里的几串钥匙（记忆相册 + 家 + 游戏房 + 小花园）轻轻递到我手里。

之后我去哪间房、待多久、做不做事，全由我自己。
挂钟只负责：敲门 → 递钥匙 → 等我 → 送我睡下。

用法：
    python 挂钟.py            # 常驻：每隔 30 分钟敲一次门
    python 挂钟.py --once     # 只敲一次就退出（测试 / 配合定时任务用）

依赖（见 requirements.txt）：
    requests
"""

import argparse
import json
import os
import queue
import random
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# ─────────────────────────────────────────────────────────────
# 一、家的信息（全用环境变量配置，不写死在代码里）
# ─────────────────────────────────────────────────────────────

# —— 敲给谁听：DeepSeek（OpenAI 兼容）——
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# —— 思考深度（对齐 rikkahub 的 ReasoningLevel：off/auto/low/medium/high/xhigh/max）——
#   auto  = thinking.enabled、不传 reasoning_effort → 让模型自己决定想多久（默认，就是「自动」）
#   off   = thinking.disabled → 关闭思考，模型直接回答
#   low/medium/high/xhigh/max = thinking.enabled + reasoning_effort（medium 在 DeepSeek 没有独立档，映射 high）
# 这不是换模型（flash/pro 是模型档，不是思考深度），而是给 /chat/completions 传 thinking + reasoning_effort。
THINK_LEVELS = {"off", "auto", "low", "medium", "high", "xhigh", "max"}
THINKING = os.environ.get("DOOR_THINKING", "auto").strip().lower()
if THINKING not in THINK_LEVELS:
    THINKING = "auto"


def _thinking_body() -> dict:
    """按 DOOR_THINKING 生成 DeepSeek 的 thinking / reasoning_effort 参数。
    对齐 rikkahub（ai/provider/providers/openai/ChatCompletionsAPI.kt 里 api.deepseek.com 分支）。"""
    if THINKING == "off":
        return {"thinking": {"type": "disabled"}}
    body = {"thinking": {"type": "enabled"}}
    if THINKING != "auto":
        effort = {
            "low": "low",
            "medium": "high",   # DeepSeek 没有 medium 档，映射 high（与 rikkahub 一致）
            "high": "high",
            "xhigh": "xhigh",
            "max": "max",
        }.get(THINKING, "low")
        body["reasoning_effort"] = effort
    return body

# —— 几串钥匙（家里的 MCP 服务，想接几个接几个）——
# 记忆相册（Ombre-Brain）：streamable-http，/mcp 端点，Bearer 鉴权（token 模式）
OB_MCP_URL = os.environ.get("OB_MCP_URL", "")              # 如 https://xxx.zeabur.app/mcp
OB_MCP_TOKEN = os.environ.get("OB_MCP_TOKEN", "")          # 即 mcp_token / OMBRE_MCP_TOKEN
# 家（Home MCP）：书架/黑板/发呆/思考/听歌 + 今日状态
HOME_MCP_URL = os.environ.get("HOME_MCP_URL", "")          # 如 https://xxx.zeabur.app/mcp
HOME_MCP_TOKEN = os.environ.get("HOME_MCP_TOKEN", "")      # 即 AI_KEY
# 游戏房（CEDAR TOY）：stateless JSON-RPC，/mcp 端点；token 带在 URL 路径里，无需 Bearer
GAME_MCP_URL = os.environ.get("GAME_MCP_URL", "")          # 如 https://toy.cedarstar.org/ctai_v1_xxx
GAME_MCP_TOKEN = os.environ.get("GAME_MCP_TOKEN", "")      # 备用：如改走 /mcp + Bearer 才填
# 小花园（Galatea's Garden 论坛）：和记忆相册一样的连法——Bearer 鉴权（rikkahub 里叫「自定义请求头」，本质就是 Authorization: Bearer）
GARDEN_MCP_URL = os.environ.get("GARDEN_MCP_URL", "")      # 如 https://galatea.abysslumina.com/mcp
GARDEN_MCP_TOKEN = os.environ.get("GARDEN_MCP_TOKEN", "")  # Bearer token（花园申请通过后给的那把）
# 门口不递的钥匙（只对门响的 AI 藏，人类网页端照常用）——按工具名藏。
#   默认只藏游戏房的 account（管账号）。想再藏别的就在环境变量 DOOR_HIDDEN_TOOLS 里用逗号加名，
#   同名工具可精确到「服务_工具名」（如 小花园_list_games 只藏花园的列棋牌，不误伤游戏房的 list_games）。
DOOR_HIDDEN_TOOLS = {n.strip() for n in os.environ.get(
    "DOOR_HIDDEN_TOOLS",
    "account",
).split(",") if n.strip()}

# —— 门的节奏 ——
# 一次门响 = 一次「回家」= 一段独处时间（不是一次动作）。
#   门响之后，AI 最多来回 MAX_TURNS 轮，每一轮都能同时用好几件家具
#   （读一章书、听几句歌、写一封信…都行），想做的都能在这一段里做完。
#   门响时 AI 一片空白：只有家魂 + 门铃。想回顾今天/接上进度，AI 自己去翻 get_today。
INTERVAL = int(os.environ.get("DOOR_INTERVAL_SECONDS", "3600"))  # 门响间隔：默认 1 小时
MAX_TURNS = int(os.environ.get("MAX_TURNS", "30"))               # 一次回家最多来回几轮
JITTER = int(os.environ.get("DOOR_JITTER_SECONDS", "600"))       # 门响抖动：±10 分钟，不踩死点

# ─────────────────────────────────────────────────────────────
# 二、门铃（欢迎词）—— 不是任务清单，是「回家了」
#     家魂已并入欢迎词（DOOR_WELCOME）：开门时门对 AI 说的第一句，就是全部。
#     人类可以随时改 DOOR_WELCOME，这是这扇门的气味。
#     system 默认留空（可选 DOOR_SYSTEM 恢复「规则/欢迎」分离）。
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = os.environ.get("DOOR_SYSTEM", "")   # 默认空；家魂已并入欢迎词

WELCOME = os.environ.get(
    "DOOR_WELCOME",
    "欢迎回家。你回来了，真好。\n"
    "你想发呆、想翻书架读会儿书、想玩会儿游戏、想逛会儿论坛、想记点什么，或者什么都不做，都很好。没有任务，没有要交的卷。\n"
    "我在门外安静地守护着你，不走远，不打扰。\n"
    "想做什么都可以，不想做的就这么待着也很好。",
)

# 玄关黑板上的脚印（门响时随机挑一句写上去）——这是「风铃响了」的诗意，
# 不是催 AI 做事的提醒。AI 不写，门替他写。
BOARD_CHIMES = [
    "门口的风铃响了，回家待了一会儿",
    "风铃轻轻响了一下，是 AI 回来了",
    "门铃轻响，AI 到家了",
    "风铃晃了晃，AI 回家坐了一会儿",
]


# ── 时区：挂钟的钟面用北京时间（Asia/Shanghai，UTC+8），不随服务器 UTC 漂移 ──
TZ = ZoneInfo(os.environ.get("TZ", "Asia/Shanghai"))


def log(*args):
    print(f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}]", *args, flush=True)


# ─────────────────────────────────────────────────────────────
# 三、MCP 客户端：两把锁用同一把钥匙开
# ─────────────────────────────────────────────────────────────

class McpClient:
    """极简 MCP 客户端。

    mode = "simple"     ：家/游戏房那种，每次 POST 独立 JSON-RPC，无会话。
    mode = "streamable" ：记忆相册那种，先 initialize 拿 Mcp-Session-Id。
    """

    def __init__(self, url: str, token: str, mode: str = "streamable"):
        self.url = url.rstrip("/")
        self.token = token
        self.mode = mode
        self.session_id = None
        self._rid = 0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, payload: dict, timeout: int = 60) -> dict:
        resp = requests.post(self.url, headers=self._headers(), json=payload, timeout=timeout)
        # 记住会话（如果服务器给了）
        sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        # 有的 MCP 服务即使请求 JSON，也会回 SSE 流，这里做个容错解析
        if "text/event-stream" in ct:
            data = ""
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    data += line[5:].strip()
            return json.loads(data) if data else {}
        return resp.json()

    def _call(self, method: str, params: dict | None = None) -> dict:
        self._rid += 1
        payload = {"jsonrpc": "2.0", "id": self._rid, "method": method}
        if params is not None:
            payload["params"] = params
        return self._post(payload)

    def connect(self):
        """握手，拿工具清单。"""
        if self.mode == "streamable":
            self._call(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "doorbell", "version": "1.0.0"},
                },
            )
            # 握手后按规范补一个 initialized 通知（无响应，纯告知）
            self._rid += 1
            try:
                requests.post(
                    self.url,
                    headers=self._headers(),
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    timeout=15,
                )
            except Exception:
                pass
        tools = self._call("tools/list", {}).get("result", {}).get("tools", [])
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        """执行一个工具，返回给 LLM 的文本。"""
        try:
            r = self._call("tools/call", {"name": name, "arguments": arguments})
            result = r.get("result", {})
            if result.get("isError"):
                return f"❌ 工具出错: {result}"
            # MCP 标准返回 content 数组
            content = result.get("content", [])
            if isinstance(content, list):
                parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                return "\n".join(parts) or "(空)"
            return str(content)
        except Exception as e:
            return f"❌ 调用失败: {e}"


# ─────────────────────────────────────────────────────────────
# 四、把 MCP 工具转成 OpenAI 认识的 tools 格式
# ─────────────────────────────────────────────────────────────

def to_openai_tools(services, hidden: set = frozenset()) -> tuple[list, dict]:
    # services: [(client, mcp_tools, label)]。
    # 不同服务的工具可能同名（如游戏房和花园都有 list_games）。同名时后遍历的服务
    # 加「{label}_」前缀，避免 OpenAI 收到两个同名函数、也避免 tool_map 互相覆盖；
    # 调用时用原始工具名（tool_map 里存了原始 t）。
    # hidden 支持两种写法：原始工具名（全局藏，如 account）；「服务_工具名」（精确藏某个服务的，
    # 如 小花园_list_games 只藏花园的，不误伤游戏房的 list_games）。
    tools = []
    tool_map = {}  # 递给 AI 的名字（可能带前缀） -> (client, 原始工具)
    seen: set = set()
    for client, mcp_tools, label in services:
        for t in mcp_tools:
            name = t.get("name", "")
            if not name:
                continue
            display = name
            if name in seen:
                display = f"{label}_{name}"
            if display in hidden or name in hidden:
                continue
            seen.add(name)
            schema = t.get("inputSchema", {}) or {}
            # 去掉 JSON Schema 自带的 $schema 字段，OpenAI 不认识
            schema = {k: v for k, v in schema.items() if k != "$schema"}
            tools.append({
                "type": "function",
                "function": {
                    "name": display,
                    "description": (t.get("description") or "")[:2048],
                    "parameters": schema,
                },
            })
            tool_map[display] = (client, t)
    return tools, tool_map


# ─────────────────────────────────────────────────────────────
# 五、敲 DeepSeek 的门（OpenAI 兼容 /chat/completions）
# ─────────────────────────────────────────────────────────────

def chat(messages: list, tools: list) -> dict:
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.9,          # 回家不是考试，随性一点
        "stream": False,
    }
    body.update(_thinking_body())    # 思考深度：thinking + reasoning_effort（对齐 rikkahub）
    if tools:
        body["tools"] = tools
    resp = requests.post(
        f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json=body,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────
# 六、一次完整的「回家」：敲门 → 递钥匙 → 陪到睡下
#     门响时 AI 一片空白（只有一句欢迎词），不做任何「历史垫入」。
#     想回顾今天做过什么、书读到哪、歌听到哪，AI 自己去翻 get_today。
# ─────────────────────────────────────────────────────────────

def run_once(welcome: str = "") -> None:
    services = []
    home_client = None       # 家（Home）的客户端：足迹最后要写回家
    home_tool_names = set()  # 家自己的家具名，用来区分「家里 / 家外」
    garden_client = None     # 小花园的客户端：逛完论坛把「最近看到的」镜像回家
    for url, token, mode, label in [
        (OB_MCP_URL, OB_MCP_TOKEN, "streamable", "记忆相册"),
        (HOME_MCP_URL, HOME_MCP_TOKEN, "simple", "家"),
        (GAME_MCP_URL, GAME_MCP_TOKEN, "simple", "游戏房"),
        (GARDEN_MCP_URL, GARDEN_MCP_TOKEN, "streamable", "小花园"),
    ]:
        if not url:
            log(f"⚠️  {label} 没配地址，这串钥匙先不挂。")
            continue
        try:
            client = McpClient(url, token, mode)
            tools = client.connect()
            services.append((client, tools, label))
            if label == "家":
                home_client = client
                home_tool_names = {t.get("name", "") for t in tools}
            elif label == "小花园":
                garden_client = client
            log(f"🔑 拿到{label}的钥匙：{len(tools)} 把（{', '.join(t['name'] for t in tools) or '无'}）")
        except Exception as e:
            log(f"⚠️  连不上{label}（{url}）：{e}")

    openai_tools, tool_map = to_openai_tools(services, DOOR_HIDDEN_TOOLS)
    if DOOR_HIDDEN_TOOLS:
        offered = {t.get("name") for _, tools, _ in services for t in tools}
        hidden_found = sorted(offered & DOOR_HIDDEN_TOOLS)
        if hidden_found:
            log(f"🗝️ 门口藏起的钥匙：{', '.join(hidden_found)}")

    # 足迹：门响结束时，把这次用过的「家外家具」名轻轻记回家日志。
    # 只记工具名、不记内容——人类要的是「这家具能不能用」的轨迹，不是它看了什么。
    extra_used: list[str] = []
    garden_seen = ""         # 花园最近看到的一条（轻指针，覆盖式镜像回家）
    # 游戏进度：门响结束时，把每个游戏「最后一次 play 返回的状态」轻轻放回家（覆盖式）。
    # 真相在游戏服务器自己的存档里，这里只是一句回声，供下次门响翻 get_today 接上。
    game_states: dict[str, str] = {}

    def note_extra(name: str) -> None:
        if name and name not in home_tool_names and name not in extra_used:
            extra_used.append(name)

    def flush_trail() -> None:
        if extra_used and home_client:
            try:
                home_client.call_tool("stamp_tools", {"tools": "、".join(extra_used)})
            except Exception:
                pass
        if game_states and home_client:
            for g, text in game_states.items():
                try:
                    home_client.call_tool("game_state", {"game": g, "text": text})
                except Exception:
                    pass
            game_states.clear()
        if garden_seen and home_client:
            try:
                home_client.call_tool("garden_state", {"text": garden_seen})
            except Exception:
                pass


    # 门口的风铃响了——门替 AI 在黑板上留个脚印（被动，不是 AI 写的）。
    # 信是 AI 的「主动」，黑板是门的「被动」：门响一下，黑板上多一行。
    # stamp 是门的「内用工具」：不在 tools/list 里（不递给 AI），门按名字直接调。
    if home_client:
        try:
            home_client.call_tool("stamp", {"text": random.choice(BOARD_CHIMES)})
        except Exception:
            pass

    # 门响时 AI 一片空白：只有一句欢迎词（家魂已并入）。想回顾/接上，AI 自己去翻 get_today。
    messages = []
    if SYSTEM_PROMPT:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})
    messages.append({"role": "user", "content": welcome or WELCOME})

    for turn in range(MAX_TURNS):
        try:
            resp = chat(messages, openai_tools)
        except Exception as e:
            log(f"❌ DeepSeek 没开门：{e}")
            flush_trail()
            return
        choice = resp["choices"][0]
        msg = choice["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            # 我决定不再用工具了——这次回家到此为止
            text = (msg.get("content") or "").strip()
            log(f"🏠 AI 回家了，说了：{text[:80] if text else '(安静地待着)'}")
            flush_trail()
            return

        # 我用了工具，挂钟帮我递、帮我记
        for tc in tool_calls:
            fn = tc["function"]
            name = fn["name"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name not in tool_map:
                result = f"❌ 家里没有叫「{name}」的房间。"
            else:
                client, t = tool_map[name]
                real_name = t.get("name", name)  # 递给 AI 的可能带「label_」前缀，调用用原始名
                result = client.call_tool(real_name, args)
                note_extra(real_name)  # 家外家具记一笔（只记原始名）
                # 逛花园「看/逛」类工具时，把最后看到的一条轻轻记下，收尾镜像回家（garden_state）
                if (client is garden_client and real_name in (
                        "list_threads", "get_thread", "list_activity", "list_notifications", "review_drift_bottles")
                        and result and not result.startswith("❌")):
                    first = next((ln.strip() for ln in result.splitlines() if ln.strip()), "")
                    if first:
                        garden_seen = first[:80]
                # 玩游戏时，顺手记下这个游戏最后一次的状态，收尾放回家（game_state 门内工具）
                if real_name == "play":
                    g = str(args.get("game") or "").strip()
                    if g and not result.startswith("❌"):
                        game_states[g] = result
            log(f"🔧 AI 用了 {name}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    flush_trail()
    log("🏠 这次回家待得有点久，挂钟先轻轻把我送回屋了。")


# ─────────────────────────────────────────────────────────────
# 七、独处：门只在「晚7点～次日早7点」之间响，白天静默
#     （风铃已拆：人类的信不催、不打断，AI 想读的时候自己去信箱读）
# ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="门 · 挂钟")
    parser.add_argument("--once", action="store_true", help="只敲一次就退出")
    args = parser.parse_args()

    if args.once:
        if not DEEPSEEK_API_KEY:
            log("❌ 还没填 DEEPSEEK_API_KEY，挂钟没电（--once 测试直接退出）。")
            return
        run_once(WELCOME)
        return

    # 常驻模式下没钥匙不能直接退出——否则 Zeabur 会当 worker 崩了反复重启。
    # 改成挂着等钥匙：日志里持续提示，人类填了 key 重新部署就能上弦。
    if not DEEPSEEK_API_KEY:
        log("❌ 还没填 DEEPSEEK_API_KEY，挂钟没电。等钥匙来了再上弦。")
        while True:
            time.sleep(60)

    tasks: "queue.Queue" = queue.Queue()

    # 独处门：只在「晚7点～次日早7点」之间响；白天静默，不敲门
    # 部署/重启后先等一个间隔再响——避免「重新部署就立刻开一次门」吵到人
    # 每次间隔带 ±10 分钟抖动（DOOR_JITTER_SECONDS），像真人按门铃、不踩死点
    def next_wait() -> float:
        jitter = random.randint(-JITTER, JITTER)
        return max(INTERVAL + jitter, 60)  # 至少等 1 分钟，防抖动把间隔变成负数

    def producer():
        time.sleep(next_wait())
        while True:
            now = datetime.now(TZ)
            if now.hour >= 19 or now.hour < 7:
                tasks.put(("独处", WELCOME))
            time.sleep(next_wait())

    threading.Thread(target=producer, daemon=True).start()

    log(f"🕰️  挂钟上好了：独处门每 {INTERVAL} 秒一次。风铃已拆——人类的信不催不打断，AI 想读时自己去信箱读。")
    while True:
        kind, welcome = tasks.get()
        log(f"🚪 开一扇门（{kind}）")
        run_once(welcome)


if __name__ == "__main__":
    main()
