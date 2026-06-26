# Cursor Remote

通过手机（或任意浏览器）远程控制 PC 上的 Cursor IDE。

- 实时查看 Cursor 的 workbench 屏幕
- 浏览**完整**的 Agent 对话历史（包括 user / assistant / 工具调用）
- 在手机上输入文字 → 同步到 Cursor 的 composer
- 一键发送 / 停止 Agent

## 架构

```
┌──────────────┐   HTTP/WS   ┌─────────────────────┐    CDP    ┌──────────┐
│  手机 / 浏览器 │ <────────> │  PC: FastAPI bridge  │ <───────> │  Cursor  │
│  (网页/Tauri) │             │  - 截图推流           │           │ (Electron)│
└──────────────┘             │  - jsonl 解析         │           └──────────┘
                             │  - 发送/停止/输入     │
                             └─────────────────────┘
```

三块代码各司其职：

| 模块 | 职责 |
|---|---|
| `cursor_cdp.py` | CDP 协议封装：找窗口、读状态、点按钮、截图、写输入框（CLI 和服务端共用，DRY） |
| `transcripts.py` | 解析 `~/.cursor/projects/<workspace>/agent-transcripts/*.jsonl`，得到结构化对话 |
| `server/main.py` | FastAPI + WebSocket 桥：HTTP REST + 实时推流 |
| `frontend/` | Vite + React + TS + Tailwind，手机优先 UI |
| `src-tauri/` | Tauri 2 配置，可打包成 Windows 桌面应用或 Android APK |
| `click_send.py` | 原 CLI 工具（保留），现已复用 `cursor_cdp.py` |

## 快速开始

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

## 原有 CLI 工具

`click_send.py` 仍然可用，行为不变：

```powershell
uv run python click_send.py --interval 5
uv run python click_send.py --list-windows
uv run python click_send.py --dry-run --once
```
