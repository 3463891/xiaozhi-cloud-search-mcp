"""浏览器保活：真正加载页面（而不是只拿 HTML 壳），并在有登录态时点「用当前地址重连」。

为什么不能只用 curl：
  curl 只拿到 SPA 的 HTML 外壳，不执行 JS，Gradio 根本没被跑起来；
  而平台的空闲回收只认「入站请求」，所以 curl 也能唤醒网关，但看不到应用是否活着。
  用浏览器打开才能真正把 Gradio 渲染出来，并操作界面上的按钮。

魔搭的登录门（关键约束）：
  魔搭空间页面（ms.show 与 modelscope.cn/studios/...）的 SPA 数据接口需要登录态，
  匿名浏览器打开后会被判为「404 页面不存在」。因此：
    · 未提供 MS_COOKIE 时 → 只打开页面做保活（降级，不报错）
    · 提供 MS_COOKIE（魔搭会话 Cookie，存 GitHub Secrets）时 → 注入后点「用当前地址重连」

安全：MS_COOKIE 只从环境变量（GitHub Secrets）读取，绝不写进日志或仓库文件。
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 只访问、不点按钮（用户未要求在此页面操作）
WORK_BUDDY = "https://xiaozhi-mcp.app.workbuddy.host/"
# 打开后要点「用当前地址重连」
MODELSCOPE = [
    "https://spng709-xiaozhi-cloud-search-v2.ms.show/",
    "https://spng709-xiaozhi-cloud-search.ms.show/",
]
BTN_TEXT = "用当前地址重连"

# 本地调试用：指向已安装的 Chromium；CI 里由 `playwright install` 安装，留空走默认
PW_EXE = os.environ.get("PW_EXECUTABLE", "").strip()


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def parse_cookie(raw: str) -> list:
    """把 'a=1; b=2' 解析成 Playwright 的 cookie 列表，同时挂到两个域名。"""
    out = []
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            continue
        for dom in (".modelscope.cn", ".ms.show"):
            out.append({"name": k, "value": v, "domain": dom, "path": "/"})
    return out


def open_page(ctx, url: str, wait_ms: int = 8000):
    """打开页面并等待渲染；返回 (page, 是否正常)。"""
    pg = ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=180000)
        pg.wait_for_timeout(wait_ms)
        return pg, True
    except Exception as e:
        log(f"打开失败 {url} -> {str(e)[:120]}")
        return pg, False


def main() -> int:
    cookie_raw = os.environ.get("MS_COOKIE", "").strip()
    cookies = parse_cookie(cookie_raw)
    log(f"魔搭登录态：{'已注入 ' + str(len(cookies)) + ' 条' if cookies else '未提供（魔搭仅做访问保活，不点按钮）'}")

    rc = 0
    launch = {"args": ["--no-sandbox"]}
    if PW_EXE:
        launch["executable_path"] = PW_EXE

    with sync_playwright() as p:
        b = p.chromium.launch(**launch)

        # ---- 1) workbuddy.host：只访问 ----
        ctx = b.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        pg, ok = open_page(ctx, WORK_BUDDY)
        if ok:
            log(f"workbuddy.host 标题 = {pg.title()!r}")
            body = (pg.inner_text("body") or "").strip()
            log("  内容摘要 =", body[:60].replace("\n", " | "))
        else:
            rc = 1
        ctx.close()

        # ---- 2) 魔搭两个空间：访问 +（有登录态时）点重连 ----
        ctx = b.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        if cookies:
            try:
                ctx.add_cookies(cookies)
            except Exception as e:
                log(f"注入 cookie 失败: {str(e)[:120]}")

        for url in MODELSCOPE:
            pg, ok = open_page(ctx, url, wait_ms=12000)
            if not ok:
                rc = 1
                ctx.close()
                continue
            title = pg.title()
            log(f"魔搭 {url.split('/')[2]} 标题 = {title!r}")

            if not cookies:
                log("  无登录态 → 仅访问保活（按预期，不算失败）")
                pg.close()
                continue

            try:
                btn = pg.locator(f"button:has-text('{BTN_TEXT}')").first
                btn.wait_for(state="visible", timeout=60000)
                btn.click(timeout=30000)
                log(f"  已点击「{BTN_TEXT}」")
                pg.wait_for_timeout(6000)   # 等结果/日志刷新
                tail = (pg.inner_text("body") or "").strip().replace("\n", " | ")
                log("  页面摘要 =", tail[:160])
            except Exception as e:
                log(f"  点击失败: {str(e)[:160]}")
                rc = 1
            pg.close()

        ctx.close()
        b.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
