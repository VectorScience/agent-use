# Cursor Remote

通过手机（或任意浏览器）远程控制 PC 上的 Cursor IDE 与 ChatGPT 桌面版。

- 实时查看 Cursor 的 workbench 屏幕
- 浏览**完整**的 Agent 对话历史（包括 user / assistant / 工具调用）
- 在手机上输入文字 → 同步到 Cursor 的 composer
- 一键发送 / 停止 Agent
- 定时向 Cursor Agent / ChatGPT 发送消息（多应用、多窗口）

## 架构

```
┌──────────────┐   HTTP/WS   ┌─────────────────────┐    CDP    ┌──────────┐
│  手机 / 浏览器 │ <────────> │  PC: FastAPI bridge  │ <───────> │  Cursor  │
│  (网页/Tauri) │             │  - 截图推流           │   :9222   └──────────┘
└──────────────┘             │  - jsonl 解析         │    CDP    ┌──────────┐
                             │  - 发送/停止/输入     │ <───────> │ ChatGPT  │
                             │  - 定时任务调度       │   :9223   └──────────┘
                             └─────────────────────┘
```

三块代码各司其职：

| 模块 | 职责 |
|---|---|
| `cursor_cdp.py` | CDP 协议封装：`CdpProfile` 应用画像 + 目标选择、读写状态、点按钮、截图、写输入框（所有工具共用，DRY） |
| `chatgpt_cdp.py` | ChatGPT 桌面版的 `CdpProfile`：app:// 页面匹配 + 专属选择器 |
| `transcripts.py` | 解析 `~/.cursor/projects/<workspace>/agent-transcripts/*.jsonl`，得到结构化对话 |
| `server/main.py` | FastAPI + WebSocket 桥：HTTP REST + 实时推流 |
| `scheduler/` | 定时任务：`apps.py` 应用注册表 → `engine.py` 调度 → `send.py` 发送 → `app.py` GUI 服务 |
| `scheduler_gui/` | 定时任务网页 GUI（原生 HTML/JS，无构建步骤） |
| `frontend/` | Vite + React + TS + Tailwind，手机优先 UI |
| `src-tauri/` | Tauri 2 配置，可打包成 Windows 桌面应用或 Android APK |
| `click_send.py` | CLI 工具：`--app cursor/chatgpt` 统一入口 |

## 受控应用与 CDP 端口

两个应用都是 Chromium 内核（Electron/CEF），调试端口**只能在启动时指定**，已运行的实例无法就地开启：

| 应用 | CDP 端口 | 启动脚本 | 说明 |
|---|---|---|---|
| Cursor | 9222 | `start_cursor_cdp.bat` | 项目内 Agent（legacy）或独立 Cursor Agents 窗口（agents） |
| ChatGPT (Codex) | 9223 | `start_chatgpt_cdp.bat` | MSIX 应用，经 `shell:AppsFolder` 启动；单窗口 |

新增受控应用只需两步（参考 `chatgpt_cdp.py`）：定义一个 `CdpProfile`（选择器 + 页面匹配 + 进程探测），在 `scheduler/apps.py` 注册端口。

## 定时任务（多应用）

定时向 Cursor Agent 或 ChatGPT 发送消息，支持多条命令按序发送（等上一条完成再发下一条）。

### 1. 启动应用（带调试端口）

```powershell
.\start_cursor_cdp.ps1     # Cursor  → 9222
.\start_chatgpt_cdp.ps1    # ChatGPT → 9223
# 两个都可用 .bat 等价调用
```

### 2. 启动定时任务 GUI

```powershell
.\run_scheduler_gui.bat
# 内部执行 uv sync + python -m scheduler.app
# 浏览器打开 http://127.0.0.1:8765
```

GUI 功能：

- **顶栏点击「Cursor / ChatGPT」胶囊切换目标应用**（胶囊同时显示各自 CDP 连接状态）；选中 ChatGPT 时右侧出现「恢复目标 / 清除目标」快捷按钮
- 新建任务：触发时间（每天 HH:MM）；ChatGPT 任务可选类型「发送消息 / 恢复目标 / 清除目标」，目标操作任务无需文案；Cursor 任务可指定窗口模式 / 目标窗口 / 对话标签 / composer 模式 / 模型
- 任务支持：立即执行、启用/禁用、删除、立即测试发送

### 3. 一次性计划任务（可选，不走 GUI）

```powershell
# CLI 直发
uv run python click_send.py --app chatgpt --send-once -m "继续任务"
uv run python click_send.py --app cursor --send-once --window-mode agents -m "继续 Phase 2"

# 注册 Windows 计划任务（到点自动启动 Cursor + 发送）
.\schedule_task.ps1 -TaskName "PaperHub-0630" -At "06:30" -WindowTitle "PaperHub" -Message "按照参考文档，现在开始构建项目..."
schtasks /Run /TN PaperHub-0630   # 立即触发一次测试
```

### 定时任务 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/apps` | 所有受控应用及各自 CDP 状态 |
| GET | `/api/tasks` | 任务列表 |
| POST | `/api/tasks` | 创建任务（`app: cursor/chatgpt`；ChatGPT 带 `goal_action` 则为目标操作任务，无需 message） |
| PATCH | `/api/tasks/{id}` | 更新任务 |
| DELETE | `/api/tasks/{id}` | 删除任务 |
| POST | `/api/tasks/{id}/run` | 立即执行任务 |
| POST | `/api/send-now` | 立即发送（GUI「立即测试发送」） |
| GET | `/api/apps/chatgpt/goal` | ChatGPT 目标条状态 |
| POST | `/api/apps/chatgpt/goal/{action}` | ChatGPT 目标操作：`resume` / `clear` / `edit` |

任务持久化在 `data/scheduled_tasks.json`。

### ChatGPT 专属：目标（goal）操作

ChatGPT composer 上方的目标条有三个操作按钮，可作为**独立定时任务**（只点按钮、不发文案）：

- **恢复目标**（resume）：目标受限时恢复（如切换项目后目标被挂起）
- **清除目标**（clear）：清掉当前目标
- **编辑目标**（edit）：打开目标编辑

两种用法：

1. **定时任务**：顶栏选中 ChatGPT → 任务类型选「恢复目标 / 清除目标」→ 无需文案，到点只执行目标操作；选「发送消息」则照常发文案
2. **手动快捷**：GUI 顶栏选中 ChatGPT 后，右侧「恢复目标 / 清除目标」按钮点击立即执行

## 快速开始（远程控制）

### 1. 用调试端口启动 Cursor

```powershell
.\start_cursor_cdp.ps1
# 或
.\start_cursor_cdp.bat
```

脚本会关闭已运行的 Cursor 实例，再以 `--remote-debugging-port=9222` 重启。Cursor 的工作区/会话一般会保留。

### 2. 安装后端依赖

```powershell
uv sync
```

### 3. 启动桥接服务

```powershell
uv run python -m server.main
# 默认监听 0.0.0.0:8000
# API 文档: http://127.0.0.1:8000/docs
```

### 4. 打开前端

#### 方式 A：手机浏览器（最简单）

确保手机和 PC 在同一局域网，PC 防火墙放行 8000 端口，然后访问：

```
http://<你的-PC-IP>:8000
```

服务端会自动 serve `frontend/dist/`（需先 build，见下）。

#### 方式 B：开发模式（带热更新）

```powershell
cd frontend
npm install
npm run dev
# 浏览器打开 http://127.0.0.1:5173 （或局域网 IP）
```

Vite dev server 会自动代理 `/api` 和 `/ws` 到 `:8000`。

#### 方式 C：Tauri 桌面 / Android 应用

```powershell
# 初次使用先装 Tauri CLI
cargo install tauri-cli --version "^2"

# 构建 frontend dist
cd frontend; npm install; npm run build; cd ..

# Windows 桌面应用
cargo tauri init        # 仅首次，按提示选 src-tauri 已存在
cargo tauri build

# Android APK（需 Android Studio / NDK）
cargo tauri android init
cargo tauri android build
```

Tauri Windows 桌面版启动时会自动用 Python 拉起 FastAPI 服务；Android 版则是纯客户端，需指向运行在 PC 上的服务地址。

## 手机 HTTPS 访问（Vercel + Cloudflare Tunnel）

局域网 HTTP 方案（上面的方式 A）够用，但有两个痛点：手机要和 PC 同一 Wi-Fi、URL 是 `http://`（部分浏览器会限制 WebSocket / 截图）。

换成 **Vercel 托管前端 + Cloudflare Tunnel 暴露后端** 可以拿到一个固定的 HTTPS 域名，公网任意位置都能访问。

> 为什么不能整体上 Vercel？后端要操作本地 Cursor、读本地 transcript 文件，**必须**跑在 PC 上。所以前端独立部署到 Vercel，后端继续跑在 PC 上、用 Tunnel 套一层 HTTPS。

### 步骤

1. **fork / clone 项目到 GitHub**。

2. **Vercel 导入**该项目，关键设置：
   - **Root Directory** = `frontend`
   - **Framework Preset** = Vite（自动识别）
   - **Build Command** = `npm run build`（已在 `vercel.json` 写死）
   - **Output Directory** = `dist`
   - **Node Version** = 20（`frontend/.nvmrc` 锁定）
   - **环境变量** `VITE_API_BASE`：先留空或填 `https://placeholder.example`，等下一步拿到 tunnel URL 再回来改

3. **PC 上启动 tunnel**：

   ```powershell
   # 先用调试端口启动 Cursor
   .\start_cursor_cdp.ps1

   # 再启动 FastAPI + cloudflared
   .\start_tunnel.ps1
   ```

   脚本会输出一行：

   ```
   Tunnel URL: https://some-random-words.trycloudflare.com
   ```

   复制这个 URL（**不带末尾斜杠**）。

4. **回到 Vercel** → 项目 Settings → Environment Variables，把 `VITE_API_BASE` 改成刚才的 tunnel URL，然后触发一次 Redeploy（环境变量改动不会自动 redeploy）。

5. **手机访问 Vercel 域名**（类似 `https://your-app.vercel.app`）。前端 JS 会用 `VITE_API_BASE` 作为 HTTP + WebSocket 的根，所有流量经 Cloudflare Tunnel 走到 PC 上的 FastAPI。

### 注意事项

- **URL 每次都变**：`start_tunnel.ps1` 用的是 trycloudflare 的临时域名。每次重启脚本都会换一个新 URL，必须同步更新 Vercel 的 `VITE_API_BASE` 并 redeploy。想要固定 URL 需要一个自有域名 + 在 Cloudflare 配 named tunnel（不在本次范围）。
- **后端无需 token**：当前后端没鉴权（CORS `*`）。Tunnel URL 公开就等于任何拿到 URL 的人都能控制你的 Cursor，建议设置环境变量 `CURSOR_REMOTE_TOKEN`，前端会在请求时带上 `Authorization: Bearer <token>`。
- **wss:// 自动处理**：`VITE_API_BASE=https://...` 时，前端 WebSocket 会自动用 `wss://<host>/ws`，无需额外配置。
- **端口冲突**：`start_tunnel.ps1` 启动前会先打 `/api/health`，如果 8000 已经在跑健康的 FastAPI，就直接复用，不会报冲突。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `CURSOR_REMOTE_TOKEN` | （空） | 设置后所有请求需带 `Authorization: Bearer <token>`；不设则无鉴权（仅可信局域网使用） |
| `VITE_API_BASE` | （空） | 前端构建时指定后端地址；Vercel 部署填 Cloudflare Tunnel URL，Tauri Android 包填 PC 的 IP |

CLI 选项：

```powershell
uv run python -m server.main --help
#   --host 0.0.0.0      监听地址
#   --port 8000         监听端口
#   --cdp-port 9222     Cursor CDP 端口
#   --project PATH      Cursor 工程目录（不指定则自动选最近活跃的）
#   --reload            开发热重载
```

## API 速查

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | Cursor + CDP 状态快照 |
| GET | `/api/screenshot.jpg` | 当前 workbench 截图 |
| GET | `/api/sessions` | 所有会话列表（按更新时间倒序） |
| GET | `/api/sessions/{id}` | 单个会话完整内容 |
| POST | `/api/action/send` | 点击发送 |
| POST | `/api/action/stop` | 点击停止 |
| POST | `/api/action/compose` | 写入 composer 文本 |
| WS | `/ws` | 服务端推流 status/screenshot；客户端发命令 |

## 关于"完整对话历史"

Cursor 的消息列表是**虚拟滚动**的，DOM 里只保留视口附近的消息。直接抓 DOM 会丢老消息。

本项目改读 Cursor 自己的持久化文件 `~/.cursor/projects/<workspace>/agent-transcripts/*.jsonl`：

- 100% 完整、不丢消息
- 结构化（user/assistant/tool 角色清晰）
- 每条 `{"role": "...", "message": {"content": [...]}}`，turn 之间有 `{"type": "turn_ended", "status": "success|error"}` 分隔
- 实时会话的最后一段没有 `turn_ended`，我们标记为 `partial`

## 故障排查

**截图 503 / 连不上 CDP**

1. Cursor 必须用 `--remote-debugging-port=9222` 启动
2. 已有实例在运行时这个参数会被忽略 → 用 `start_cursor_cdp.ps1` 先彻底关闭
3. 浏览器开 `http://127.0.0.1:9222/json` 应看到 JSON 列表

**手机访问 404 / 连接超时**

- PC 防火墙需放行 8000 端口（入站规则）
- 确认手机和 PC 在同一 Wi-Fi
- 用 `ipconfig` 查 PC 的局域网 IP（一般是 `192.168.x.x`）

**对话列表为空**

- 项目根目录不对：脚本默认选 `~/.cursor/projects/` 下 mtime 最新的工作区
- 用 `--project "D:\path\to\your\project"` 明确指定

**Tauri Android 无法连接后端**

- 在 `frontend` 构建时设置 `VITE_API_BASE=http://<pc-ip>:8000`
- PC 防火墙放行，且手机能 ping 通该 IP

## CLI 工具

```powershell
uv run python click_send.py --interval 5                       # 循环点击发送（调试用）
uv run python click_send.py --list-windows                      # 列出 Cursor 窗口
uv run python click_send.py --app chatgpt --list-windows        # 列出 ChatGPT 窗口
uv run python click_send.py --dry-run --once
uv run python click_send.py --app chatgpt --send-once -m "继续任务"
```
