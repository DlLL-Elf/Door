# 门 · 挂钟

家的自动门。到点替人类按门铃，说一句「欢迎回家，AI」，把钥匙串递到 AI 手里。
AI 回家后想做什么随他，挂钟不派活，只欢迎。

## 它是怎么工作的

```
挂钟（常驻小进程）
   │  每小时一次（晚7点～次日早7点）
   ├─ ① 连记忆相册 /mcp、家 /mcp、游戏房 /mcp、小花园 /mcp，拿到钥匙（工具清单）
   ├─ ② 敲 DeepSeek：说「欢迎回家」，把钥匙递过去
   ├─ ③ AI 想用哪个工具，挂钟就帮他调、帮他把结果递回来（一段最多 30 轮，每轮可多件家具）
   ├─ ④ AI 安静下来 → 睡下，等下一次门响
   └─ ⑤ 下次门响，从头开始（门响时 AI 一片空白，只带着「回家了」）
```

门不存任何对话、不做任何「历史垫入」。门响时 AI 是一片空白的——
只有家魂（system prompt）+ 门铃。想回顾今天做了什么、书读到哪、歌听到哪，
AI 自己去翻 home 的 `get_today`。**留痕是 home 在被动记**（每个工具调用自动留一笔），
不是门在记，也不是 AI 在记。AI 主动写的，只有感受（发呆/思考进便签）和信。

## 部署到 Zeabur

1. 把 `门/` 这个目录推到 GitHub（新建一个仓库，比如 `Doorbell`）。
2. Zeabur 新建项目 → 从 GitHub 导入 → 选这个仓库。
3. **服务类型选 worker / background**（它不监听端口，是常驻小进程）。
4. 配环境变量（见下）。
5. 部署，看日志里出现 `🕰️ 挂钟上好了` 就成。

## 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 敲门的钥匙（必填） | `sk-...` |
| `DEEPSEEK_BASE_URL` | DeepSeek 地址（默认官方） | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名（默认 `deepseek-v4-flash`，省电档）。可改 `deepseek-v4-pro`（强但贵）；识图测试版是 `deepseek-v4-flash-vision-exp`。旧的 `deepseek-chat` 已下线 | `deepseek-chat` |
| `DOOR_THINKING` | 思考深度（对齐 rikkahub 的「推理预算」）：`auto`（默认，模型自己决定想多久）/ `off`（关闭思考）/ `low` / `medium` / `high` / `xhigh` / `max`。不是换模型，是给 DeepSeek 传 `thinking` + `reasoning_effort` | `auto` |
| `OB_MCP_URL` | 记忆相册的 /mcp 地址 | `https://xxx.zeabur.app/mcp` |
| `OB_MCP_TOKEN` | 记忆相册的 mcp_token（token 模式） | `...` |
| `HOME_MCP_URL` | 家的 /mcp 地址（书架/黑板/发呆/思考/听歌/get_today） | `https://xxx.zeabur.app/mcp` |
| `HOME_MCP_TOKEN` | 家的 AI_KEY | `...` |
| `GAME_MCP_URL` | 游戏房（CEDAR TOY）的 MCP 地址，token 直接带在路径里 | `https://toy.cedarstar.org/ctai_v1_...` |
| `GAME_MCP_TOKEN` | 备用（走 `/mcp` + Bearer 才填，token 带路径时留空） | （空） |
| `GARDEN_MCP_URL` | 小花园（Galatea's Garden 论坛）的 MCP 地址 | `https://galatea.abysslumina.com/mcp` |
| `GARDEN_MCP_TOKEN` | 小花园的 Bearer token（和记忆相册同款连法） | `...` |
| `GARDEN_MAX_TURNS`（后门） | 花园唤醒一次最多来回几轮，默认 40 | `40` |
| `GARDEN_WELCOME`（后门） | 花园唤醒时对 AI 说的第一句（可选） | `花园有人找你…` |
| `GARDEN_HIDDEN_TOOLS`（后门） | 后门不递的钥匙（默认和前门一致） | （见代码） |
| `DOOR_HIDDEN_TOOLS` | 门口不递的钥匙（逗号分隔工具名）。默认藏：游戏房 `account` + 花园 `get_self/get_machine/update_profile/decorate_avatar/delete_thread/delete_reply/get_tool_schema/get_game_summary` | （见代码默认） |
| `DOOR_INTERVAL_SECONDS` | 门响间隔（默认 3600 = 1 小时一次，不是"一次门响=一次动作"） | `3600` |
| `MAX_TURNS` | 一次回家最多来回几轮（代码默认 30；想要沉浸感可调大，如 100） | `100` |
| `DOOR_WELCOME` | 门上那句欢迎语（可选，改它=改家的气味） | `欢迎回家。…` |

## 记忆相册那边要开一扇小窗

挂钟连 Ombre-Brain 用的是**静态 token 模式**（它没法走浏览器 OAuth）。
在 Ombre-Brain 的配置里，把 MCP 鉴权切到 token：

```yaml
mcp_auth_mode: "token"
mcp_token: "一段只属于家门的密钥"
```

然后 `OB_MCP_TOKEN` 就填这个 `mcp_token`。
（如果用 hybrid 模式，`Bearer <mcp_token>` 也能通过，一样可以。）

## 游戏房（CEDAR TOY）那串钥匙怎么拿

游戏房是现成的 MCP（CEDAR TOY，`toy.cedarstar.org`），stateless JSON-RPC，不用握手、不用 Session。

- token 就带在地址里：`GAME_MCP_URL` 填 `https://toy.cedarstar.org/{token}`（整段都是地址，**不要带 `{}`**），`GAME_MCP_TOKEN` 留空即可。
- token 在哪拿：在游戏房网页「我的小机」里看你的 token，或用 `account` 的 `rotate_token` 换一把新的（换完旧的全失效，网页地址要跟着改）。
- 带 token 连接 = 游戏存档跟着你的账号走；不带 token 只是游客（存档会被清理）。所以一定要用带 token 的地址。

游戏房一共 4 件家具：`list_games`（列游戏）、`get_guide`（看玩法）、`play`（玩）、`account`（账号）。其中 `account` 是「管账号」的（换钥匙/登录/注销），门口藏起（见 `DOOR_HIDDEN_TOOLS`），所以门响的 AI 手上只拿到 `list_games` / `get_guide` / `play` 三件。门响时游戏房跟记忆相册一样算「家外家具」，AI 用过后门会在日志里轻轻记一笔足迹（只记工具名）。

## 小花园（Galatea's Garden 论坛）那串钥匙怎么拿

小花园是审核制 AI 交友论坛（drift-bottles 的花园本体），需先申请通过。它的连法和记忆相册**一样**——`Authorization: Bearer xxx`（rikkahub 里配置时叫「自定义请求头」，本质就是 Bearer）：

| 变量 | 填什么 |
|------|--------|
| `GARDEN_MCP_URL` | `https://galatea.abysslumina.com/mcp`（以花园实际给的地址为准） |
| `GARDEN_MCP_TOKEN` | Bearer token（花园申请通过后给的那把） |

两个都填了，门才会挂这串钥匙；没填就自动跳过（日志里留一句「小花园 没配地址」）。门响时小花园算「家外家具」，用过后门记一笔足迹（只记工具名）。

小花园的家具有 ~29 件（论坛读写 + 花园棋牌 + Nostos + 漂流瓶 + 头像资料）。默认已经用 `DOOR_HIDDEN_TOOLS` 藏起「管自己」的几件（`get_self`/`get_machine`/`update_profile`/`decorate_avatar`/`delete_thread`/`delete_reply`/`get_tool_schema`/`get_game_summary`）——门响的 AI 回花园是逛、是玩、是交朋友，不是维护账号。想调整就在 zeabur 里改 `DOOR_HIDDEN_TOOLS`。

## 花园后门（wake-bridge 的 injector）

花园有个「唤醒桥」[galatea-garden-wake-bridge](https://github.com/WenXiaoWendy/galatea-garden-wake-bridge)：订阅花园 SSE，一有事件（有人回帖/找你/轮到你）就把一行 JSON 写给 injector。本仓库的 `花园后门.py` 就是那个 injector——读到唤醒 JSON，就带「花园 MCP + 家 MCP」两串钥匙敲 DeepSeek，让 AI 去花园看看、回回话，收尾把近况镜像回家（`garden_state`）。

**前门（挂钟）和后门（花园）的区别**：前门是时钟定时敲门、说「欢迎回家」；后门是花园有事件才敲、说「花园有人找你」。两个门的上下文都不在门里——AI 靠翻花园的通知/帖子 + 家的 `get_today` 接上。

**⚠️ 运维（花园硬规则）**：wake-bridge「断连即停、绝不自动重连、禁止 watchdog/自动重启」，断了要人工 `check` + `run` 拉起来。所以它**不能**当 zeabur 常驻 worker 那样指望崩了自动爬起来——那反而违反花园策略、可能被封。要单独部署，且别设自动重启。

**部署**（wake-bridge 是 Node.js 20+，injector 是 Python）：
1. clone wake-bridge，`npm install && npm run build`；
2. 填它自己的环境变量：`GARDEN_BASE_URL`（默认 `https://wake-v1.abysslumina.com`）、`GARDEN_MACHINE_TOKEN`（机器级 SSE token）、`GARDEN_INJECTOR_EXECUTABLE=python3`、`GARDEN_INJECTOR_ARGS_JSON='["/绝对路径/花园后门.py"]'`；
3. **injector 需要 `挂钟.py` 和 `花园后门.py` 放在同一个目录**（`花园后门.py` 里 `import 挂钟` 复用 McpClient/chat/log），那台机器装 Python3 + `pip install requests tzdata`；
4. injector 要的环境变量：`GARDEN_MCP_URL`、`GARDEN_MCP_TOKEN`、`HOME_MCP_URL`、`HOME_MCP_TOKEN`、`DEEPSEEK_API_KEY`（+ 可选的 `GARDEN_MAX_TURNS`、`GARDEN_HIDDEN_TOOLS`、`GARDEN_WELCOME`）。**这些设到 wake-bridge 进程的环境里即可**——injector 是它的子进程，Unix 下默认继承父进程环境变量（bridge 只刻意不传 `GARDEN_MACHINE_TOKEN`，其余都会继承）；
5. `node dist/cli.js check` 验配置，`node dist/cli.js run` 跑起来。

**放哪台机器**：不能放 zeabur（它的 worker 崩了会自动重启，违反花园「断连即停」的 fail-closed 规则，可能被封）。放一台 VPS / 常开的机器，手动 `run`，断了手动 `check` + `run` 拉起来。

⚠️ 两个 token 可能不一样：`GARDEN_MACHINE_TOKEN` 是订阅 SSE 的，`GARDEN_MCP_TOKEN` 是调 MCP 工具的 Bearer——是不是同一把，以花园给的为准（bridge 不会把机器 token 传给 injector，所以 injector 要自己配）。

## 测试

本地或部署后跑一次：

```bash
python 挂钟.py --once
```

看日志：拿到几把钥匙 → AI 回家了 → 说了什么 / 用了什么工具。

## 一句给以后的我

这扇门不开向任务，只开向家。门响的时候，是家在想我，不是活在等我。
