# 小智 MCP 云搜索桥（XiaoZhi Cloud Search Bridge）

为小智 ESP32 AI 硬件提供自托管的联网搜索 MCP 服务，
对标 facaiai.cn 的「联网搜索」能力，免费且不限次数。

## 能力

| 工具 | 说明 |
| --- | --- |
| `web_search` | 联网搜索，Tavily 主用 + DuckDuckGo 自动兜底 |
| `fetch_url` | 抓取网页正文 |

## 架构

```
小智设备 ──> xiaozhi.me 云端 ──(MCP over WebSocket, 云端主动下发)──> 本服务
```

本服务以 MCP Server 角色长连小智 MCP 接入点（`wss://api.xiaozhi.me/mcp/?token=...`），
无需公网入站端口，可部署在任何能出网的免费容器平台（Render / 魔搭创空间 / 本地常驻）。

## 环境变量

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `XIAOZHI_MCP_WSS` | 是 | 小智控制台 → 智能体 → 扩展能力 → MCP 接入点 |
| `TAVILY_API_KEY` | 否 | Tavily 搜索 key（免费 1000 次/月），不填则用 DuckDuckGo |
| `TAVILY_API_KEY2` | 否 | 备用 key，主 key 超限自动切换 |
| `PORT` | 否 | 健康检查端口，云平台自动注入 |

## Render 一键部署

1. New → Web Service → Public Git repository，填入本仓库地址
2. Runtime: Python 3；Build Command: `pip install -r requirements.txt`
3. Start Command: `python mcp_search_bridge.py`
4. Instance Type: Free
5. Environment 里添加 `XIAOZHI_MCP_WSS`（和可选的 Tavily key）

健康检查：`GET /` 返回 `{"status":"ok"}`。
