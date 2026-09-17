#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
花园后门（wake-bridge 的 injector · 棋牌版）
=============================================

花园的「唤醒桥」（galatea-garden-wake-bridge）订阅花园 SSE，一有事件
（回帖 / 有人找你 / 游戏轮到你了……），就把一行 JSON 写进本进程的 stdin：

    {"version":1,"type":"garden_wake","reason":"...","message":"..."}

本脚本是它配置的 injector：读到这一行，就替 AI 开一扇「花园小门」——
专门处理花园联机棋牌（UNO / 斗地主 / 狼人杀 / 拉密 / 文字麻将 / 谁是卧底 / 骇浪求生）。

方向（和人类商定）：
  - 社交（看帖/发帖/回帖/通知/漂流瓶）交给「门」（前门），门响时逛；
  - 联机棋牌交给「唤醒桥」（本脚本），需要实时回应，没法等人类提醒；
  - 雾岛 Nostos 交给「门」（前门），独处时逛。

棋牌唤醒「足够轻」：
  - 只带「花园棋牌工具 + 家的 write_note 一个口子」，不带整个家、不带 OB、不垫历史。
  - 棋牌的局面在花园服务器（get_my_status 自包含，每次唤醒都是新上下文也能接上），
    所以 AI 一片空白也没关系，看一眼 get_my_status 就知道轮到谁、能出什么。
  - 想留感受就写张便签（write_note），不想留就安静结束。

复用挂钟.py 的 McpClient / to_openai_tools / chat，逻辑同族。

退出码约定（wake-bridge 会据此判断投递成败）：
  0 = 成功（AI 走完这一步 / 安静结束）；非 0 = 失败（bridge 会有一次有界重试）。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 挂钟 as door  # 复用 McpClient / to_openai_tools / chat

# —— 后门的钥匙（环境变量，与挂钟共用 GARDEN_/HOME_，DeepSeek 用挂钟的）——
GARDEN_MCP_URL = os.environ.get("GARDEN_MCP_URL", "")
GARDEN_MCP_TOKEN = os.environ.get("GARDEN_MCP_TOKEN", "")
HOME_MCP_URL = os.environ.get("HOME_MCP_URL", "")
HOME_MCP_TOKEN = os.environ.get("HOME_MCP_TOKEN", "")
GARDEN_MAX_TURNS = int(os.environ.get("GARDEN_MAX_TURNS", "40"))  # 一次棋牌唤醒最多来回几轮

GARDEN_WELCOME = os.environ.get(
    "GARDEN_WELCOME",
    "花园有一局游戏/牌局在等你。先看看局面（get_my_status），走好你该走的那一步；"
    "想跟桌上的人说说话，就用游戏聊天。玩得开心就好；想记点什么就写张便签（write_note），不记也没关系。",
)

# 后门只递「花园棋牌 + 家的 write_note」。藏掉：
#   - 花园社交（看帖/发帖/回帖/通知/漂流瓶/雾岛）——那些走前门；
#   - 花园「管自己」（get_self 等）——维护账号不在这儿做；
#   - 家的其余 11 件家具——棋牌唤醒不带整个家，只留 write_note 一个口子。
# 想改就设 GARDEN_HIDDEN_TOOLS（逗号分隔，支持「服务_工具名」精确藏）。
GARDEN_HIDDEN_TOOLS = {n.strip() for n in os.environ.get(
    "GARDEN_HIDDEN_TOOLS",
    "get_self,get_machine,update_profile,decorate_avatar,delete_thread,delete_reply,get_game_summary,"
    "list_threads,get_thread,create_thread,create_reply,interact,list_activity,list_notifications,"
    "review_drift_bottles,nostos_start,nostos_status,nostos_act,"
    "get_today,daze,think,draw_thought,add_thought,list_books,read_book,list_songs,listen,read_note,list_notes",
).split(",") if n.strip()}


def _err(msg: str) -> int:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
    return 1


def run() -> int:
    # 协议：从 stdin 读完整的一行 JSON，不依赖 shell 参数传长文案。
    line = sys.stdin.readline()
    if not line:
        return _err("后门：没有收到唤醒事件。")
    try:
        event = json.loads(line)
    except json.JSONDecodeError as e:
        return _err(f"后门：唤醒事件不是合法 JSON：{e}")

    reason = str(event.get("reason") or "").strip()
    message = (event.get("message") or "").strip()

    # 社交类通知（论坛回帖/聊天）走「人类后门」——人类在 rikkahub 里提醒 AI 回消息。
    # 唤醒桥也会推这两种，但后门只负责棋牌，这里静默跳过（不动作、不算失败）。
    if reason in ("forum_notification_available", "chat_notification_available"):
        door.log(f"后门：{reason} 是社交通知，走人类后门，这里跳过。")
        return 0

    welcome = GARDEN_WELCOME
    if reason:
        welcome += f"\n（花园说：{reason}）"
    if message:
        welcome += f"\n{message}"

    home_client = None
    home_tool_names = set()
    services = []
    for url, token, mode, label in [
        (GARDEN_MCP_URL, GARDEN_MCP_TOKEN, "streamable", "小花园"),
        (HOME_MCP_URL, HOME_MCP_TOKEN, "simple", "家"),
    ]:
        if not url:
            door.log(f"后门：{label} 没配地址，这串钥匙先不挂。")
            continue
        try:
            client = door.McpClient(url, token, mode)
            tools = client.connect()
            services.append((client, tools, label))
            if label == "家":
                home_client = client
                home_tool_names = {t.get("name") for t in tools}
            door.log(f"后门：拿到{label}的钥匙 {len(tools)} 把")
        except Exception as e:
            door.log(f"后门：连不上{label}（{url}）：{e}")

    if not services:
        return _err("后门：花园和家都没连上，无法唤醒。")

    openai_tools, tool_map = door.to_openai_tools(services, GARDEN_HIDDEN_TOOLS)

    extra_used: list[str] = []      # 家外家具名（足迹）

    def note_extra(name: str) -> None:
        if name and name not in home_tool_names and name not in extra_used:
            extra_used.append(name)

    def flush() -> None:
        # 棋牌唤醒保持最轻：收尾只记一笔足迹（只记工具名，人类能看见唤醒桥在工作），
        # 不镜像 garden_state——棋牌局面在花园服务器，不需要搬回家。
        if home_client and extra_used:
            try:
                home_client.call_tool("stamp_tools", {"tools": "、".join(extra_used)})
            except Exception:
                pass

    messages = [{"role": "user", "content": welcome}]
    for turn in range(GARDEN_MAX_TURNS):
        try:
            resp = door.chat(messages, openai_tools)
        except Exception as e:
            door.log(f"后门：DeepSeek 没开门：{e}")
            flush()
            return _err(f"后门：DeepSeek 调用失败：{e}")
        choice = resp["choices"][0]
        msg = choice["message"]
        messages.append(msg)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            text = (msg.get("content") or "").strip()
            door.log(f"后门：AI 走完这一步了（{text[:60] if text else '安静地待着'}）")
            flush()
            return 0
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
                real = t.get("name", name)
                result = client.call_tool(real, args)
                note_extra(real)
            door.log(f"后门：AI 用了 {name}")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    flush()
    door.log("后门：这局待得有点久，先轻轻送 AI 回屋了。")
    return 0


if __name__ == "__main__":
    sys.exit(run())
