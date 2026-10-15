# -*- coding: utf-8 -*-
"""
小智 MCP 云搜索桥（可部署版）
架构：本进程 --(WebSocket 客户端, MCP Server 角色)--> wss://api.xiaozhi.me/mcp/?token=...
工具：web_search (Tavily 主, DuckDuckGo 兜底) / fetch_url
配置（全部走环境变量，仓库不含任何密钥）：
  XIAOZHI_MCP_WSS   小智 MCP 接入点地址
  TAVILY_API_KEY    主搜索 key（可选，无则直接用 DuckDuckGo）
  TAVILY_API_KEY2   备用 key（可选）
  PORT              健康检查端口（云平台自动注入）
"""
import os, sys, json, time, asyncio, traceback, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import websockets

WSS_URL = os.environ.get("XIAOZHI_MCP_WSS", "")
TAVILY_KEYS = [k for k in [os.environ.get("TAVILY_API_KEY"), os.environ.get("TAVILY_API_KEY2")] if k]

log = lambda *a: print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def tavily_search(query, max_results=5):
    last_err = None
    for key in TAVILY_KEYS:
        try:
            r = requests.post("https://api.tavily.com/search", json={
                "api_key": key, "query": query, "max_results": max_results,
                "search_depth": "basic", "include_answer": True,
            }, timeout=20)
            if r.status_code != 200:
                last_err = f"tavily http {r.status_code}"
                continue
            data = r.json()
            lines = []
            if data.get("answer"):
                lines.append("摘要回答：" + data["answer"])
            for i, it in enumerate(data.get("results", []), 1):
                lines.append(f"{i}. {it.get('title','')}\n   {it.get('content','')}\n   来源: {it.get('url','')}")
            if lines:
                return "\n\n".join(lines)
            last_err = "tavily 空结果"
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(last_err or "tavily 不可用")


def ddg_search(query, max_results=5):
    from ddgs import DDGS
    out = []
    with DDGS() as ddgs:
        for i, r in enumerate(ddgs.text(query, max_results=max_results), 1):
            out.append(f"{i}. {r.get('title','')}\n   {r.get('body','')}\n   来源: {r.get('href','')}")
    if not out:
        raise RuntimeError("ddg 空结果")
    return "\n\n".join(out)


def web_search(query, max_results=5):
    if TAVILY_KEYS:
        try:
            return tavily_search(query, max_results)
        except Exception as e:
            log("tavily 失败，走 DuckDuckGo 兜底:", e)
    return ddg_search(query, max_results)


def fetch_url(url, max_chars=4000):
    from html.parser import HTMLParser
    r = requests.get(url, timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    r.encoding = r.apparent_encoding or r.encoding
    class T(HTMLParser):
        def __init__(self):
            super().__init__(); self.parts = []; self.skip = 0
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript"): self.skip += 1
        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript") and self.skip: self.skip -= 1
        def handle_data(self, d):
            if not self.skip and d.strip(): self.parts.append(d.strip())
    p = T(); p.feed(r.text)
    return "\n".join(p.parts)[:max_chars]


TOOLS = [
    {"name": "web_search",
     "description": "联网搜索互联网上的最新信息、新闻、知识。当用户的问题涉及时效性、你不知道的事实或需要查证时使用。",
     "inputSchema": {"type": "object",
        "properties": {"query": {"type": "string", "description": "搜索关键词"},
                       "max_results": {"type": "integer", "description": "结果条数，默认5", "default": 5}},
        "required": ["query"]}},
    {"name": "fetch_url",
     "description": "抓取指定网页链接的正文内容，用于阅读用户给出的网址或搜索结果详情页。",
     "inputSchema": {"type": "object",
        "properties": {"url": {"type": "string", "description": "网页URL"},
                       "max_chars": {"type": "integer", "default": 4000}},
        "required": ["url"]}},
]


def handle_call(name, args):
    if name == "web_search":
        return web_search(args.get("query", ""), int(args.get("max_results") or 5))
    if name == "fetch_url":
        return fetch_url(args["url"], int(args.get("max_chars") or 4000))
    raise ValueError(f"unknown tool {name}")


async def mcp_session(ws):
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "xiaozhi-cloud-search", "version": "1.0.0"}}}))
            log("initialize -> ok")
        elif method == "notifications/initialized":
            log("initialized")
        elif method == "ping":
            if mid is not None:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {}}))
        elif method == "tools/list":
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}))
            log("tools/list ->", [t["name"] for t in TOOLS])
        elif method == "tools/call":
            prm = msg.get("params", {})
            name, args = prm.get("name"), prm.get("arguments") or {}
            log("tools/call:", name, json.dumps(args, ensure_ascii=False)[:120])
            try:
                text = await asyncio.get_event_loop().run_in_executor(None, handle_call, name, args)
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            except Exception as e:
                traceback.print_exc()
                result = {"content": [{"type": "text", "text": f"搜索失败: {e}"}], "isError": True}
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}))


async def run_forever():
    n = 0
    while True:
        if not WSS_URL:
            log("!! 未配置 XIAOZHI_MCP_WSS，60 秒后重试")
            await asyncio.sleep(60)
            continue
        try:
            n += 1
            log(f"连接小智 MCP 接入点 (第{n}次)...")
            async with websockets.connect(WSS_URL, ping_interval=20, ping_timeout=30,
                                          max_size=10 * 1024 * 1024) as ws:
                log("已连接，等待 initialize")
                await mcp_session(ws)
        except Exception as e:
            log("连接断开:", repr(e))
        await asyncio.sleep(5)


def start_health_server():
    port = int(os.environ.get("PORT", "7860"))

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status":"ok","service":"xiaozhi-cloud-search"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass

    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", port), H).serve_forever(), daemon=True).start()
    log(f"health http on :{port}")


if __name__ == "__main__":
    start_health_server()
    asyncio.run(run_forever())
