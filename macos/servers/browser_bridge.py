#!/usr/bin/env python3
"""browser_bridge.py — MCP stdio 服务器，把「你正在用的浏览器」暴露给 agent。

支持 Safari / Google Chrome / Microsoft Edge。由 DSH 宿主进程拉起，
因此不受 bash 沙盒的 Apple Events 限制。

能力矩阵（实测）：

| 功能        | Safari            | Chrome / Edge              |
|-------------|-------------------|----------------------------|
| 列出标签    | ✅ AppleScript    | ✅ AppleScript             |
| 当前标签    | ✅                | ✅                         |
| 读正文      | ✅ text of document（无需开关） | ⚠️ 需要「允许 Apple 事件中的 JavaScript」 |
| 打开网址    | ✅                | ✅                         |

Firefox 没有可用的 AppleScript 字典，不在支持范围。
"""

from __future__ import annotations
import json
import re
import subprocess
import sys
import time

try:  # 输出被重定向时编码可能不是 UTF-8，会让中文变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

OSA = "/usr/bin/osascript"
TIMEOUT = 25
MAX_TEXT = 20000

APPS = {
    "safari": {"app": "Safari", "proc": "Safari"},
    "chrome": {"app": "Google Chrome", "proc": "Google Chrome"},
    "edge": {"app": "Microsoft Edge", "proc": "Microsoft Edge"},
}

JS_HINT = {
    "chrome": "Chrome 菜单：查看 > 开发者 > 允许 Apple 事件中的 JavaScript",
    "edge": "Edge 菜单：查看 > 开发者 > 允许 Apple 事件中的 JavaScript",
}

TOOLS = [
    {
        "name": "list_tabs",
        "description": ("列出某个浏览器里所有窗口和标签页（标题 + URL）。"
                        "browser 可选 safari / chrome / edge，默认 safari。"),
        "inputSchema": {
            "type": "object",
            "properties": {"browser": {"type": "string",
                                       "enum": ["safari", "chrome", "edge"]}},
        },
    },
    {
        "name": "current_tab",
        "description": "返回该浏览器最前面窗口的当前标签页标题和 URL。",
        "inputSchema": {
            "type": "object",
            "properties": {"browser": {"type": "string",
                                       "enum": ["safari", "chrome", "edge"]}},
        },
    },
    {
        "name": "get_text",
        "description": ("读取该浏览器当前标签页的正文纯文本。Safari 无需任何设置；"
                        "Chrome/Edge 需要开启「允许 Apple 事件中的 JavaScript」。"),
        "inputSchema": {
            "type": "object",
            "properties": {"browser": {"type": "string",
                                       "enum": ["safari", "chrome", "edge"]}},
        },
    },
    {
        "name": "open_url",
        "description": "在该浏览器新建标签页打开网址并把窗口提到前台。只接受 http/https。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "browser": {"type": "string",
                            "enum": ["safari", "chrome", "edge"]},
                "url": {"type": "string", "description": "要打开的网址"},
            },
            "required": ["url"],
        },
    },
]


def run_osa(script: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run([OSA, "-e", script], capture_output=True,
                              text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, "AppleScript 超时（%d 秒）" % TIMEOUT

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        if "-1743" in err or "not authorized" in err.lower():
            return False, ("没有被允许控制该浏览器。系统设置 > 隐私与安全性 > 自动化，"
                           "勾选 DeepSeek Harness。")
        if "-1719" in err or "无效的索引" in err:
            return False, "该浏览器没有打开的窗口。"
        return False, err or ("osascript 退出码 %d" % proc.returncode)
    return True, out


def is_running(proc_name: str) -> bool:
    try:
        return subprocess.run(["/usr/bin/pgrep", "-x", proc_name],
                              capture_output=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return True  # 判断不了就别拦着


def resolve(browser: str | None) -> tuple[str, dict] | None:
    key = (browser or "safari").lower()
    if key not in APPS:
        return None
    return key, APPS[key]


def script_list_tabs(app: str, chromium: bool) -> str:
    title_prop = "title" if chromium else "name"
    return """
tell application "%s"
  set out to ""
  repeat with w from 1 to count of windows
    repeat with t from 1 to count of tabs of window w
      set out to out & w & (ASCII character 9) & t & (ASCII character 9) & ¬
        (%s of tab t of window w) & (ASCII character 9) & ¬
        (URL of tab t of window w) & (ASCII character 10)
    end repeat
  end repeat
  return out
end tell
""" % (app, title_prop)


def script_current_tab(app: str, chromium: bool) -> str:
    tab_ref = "active tab" if chromium else "current tab"
    title_prop = "title" if chromium else "name"
    return """
tell application "%s"
  if (count of windows) is 0 then return ""
  return (%s of %s of front window) & (ASCII character 9) & ¬
    (URL of %s of front window)
end tell
""" % (app, title_prop, tab_ref, tab_ref)


def script_text(app: str, chromium: bool) -> str:
    if chromium:
        return """
tell application "%s"
  if (count of windows) is 0 then return ""
  execute active tab of front window javascript "document.body.innerText"
end tell
""" % app
    return """
tell application "%s"
  if (count of windows) is 0 then return ""
  return text of document 1
end tell
""" % app


def script_open(app: str, chromium: bool, url: str) -> str:
    safe = url.replace("\\", "").replace('"', "%22")
    if chromium:
        # Chromium 的 AppleScript 里 `set active tab index to ...` 会报
        # -10006 / "不能将 index of tab 设置为 index"（实测 Edge 156 必复现）。
        # `open location` 是字典里的标准命令，会在前台窗口新建标签，最稳。
        return """
tell application "%s"
  activate
  open location "%s"
end tell
""" % (app, safe)
    return """
tell application "%s"
  if (count of windows) is 0 then
    make new document with properties {URL:"%s"}
  else
    tell front window
      set t to make new tab with properties {URL:"%s"}
      set current tab to t
    end tell
  end if
  activate
end tell
""" % (app, safe, safe)


def parse_tabs(out: str) -> list[dict]:
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        rows.append({"window": int(parts[0]), "tab": int(parts[1]),
                     "title": parts[2], "url": "\t".join(parts[3:])})
    return rows


def guard(browser: str | None) -> tuple[str, dict] | str:
    resolved = resolve(browser)
    if not resolved:
        return "不支持的浏览器：%s（可选 safari / chrome / edge）" % browser
    key, info = resolved
    if not is_running(info["proc"]):
        return "%s 当前没有在运行。" % info["app"]
    return key, info


def tool_list_tabs(args: dict) -> str:
    g = guard(args.get("browser"))
    if isinstance(g, str):
        return g
    key, info = g
    ok, out = run_osa(script_list_tabs(info["app"], key != "safari"))
    if not ok:
        return "读取失败：" + out
    tabs = parse_tabs(out)
    if not tabs:
        return "%s 没有打开的标签页。" % info["app"]
    return json.dumps({"browser": key, "count": len(tabs), "tabs": tabs},
                      ensure_ascii=False)


def tool_current_tab(args: dict) -> str:
    g = guard(args.get("browser"))
    if isinstance(g, str):
        return g
    key, info = g
    ok, out = run_osa(script_current_tab(info["app"], key != "safari"))
    if not ok:
        return "读取失败：" + out
    if not out:
        return "%s 没有打开的窗口。" % info["app"]
    title, _, url = out.partition("\t")
    return json.dumps({"browser": key, "title": title, "url": url},
                      ensure_ascii=False)


def tool_get_text(args: dict) -> str:
    g = guard(args.get("browser"))
    if isinstance(g, str):
        return g
    key, info = g

    # 刚 open_url 或刚切标签时页面可能还没渲染完，读出来是空串。
    # 重试几次再下结论，避免把「还在加载」误报成「没有正文」。
    out = ""
    for attempt in range(3):
        ok, out = run_osa(script_text(info["app"], key != "safari"))
        if not ok:
            hint = JS_HINT.get(key)
            if hint and "javascript" in out.lower():
                return "读取失败：需要先开启开关 —— %s" % hint
            return "读取失败：" + out
        if out.strip():
            break
        if attempt < 2:
            time.sleep(0.8)

    if out.startswith("__"):
        return "%s 没有打开的窗口。" % info["app"]
    if not out.strip():
        return "%s 当前页面没有可读文本（可能还在加载，稍后重试）。" % info["app"]
    text = re.sub(r"\n{3,}", "\n\n", out)
    return text[:MAX_TEXT]


def tool_open_url(args: dict) -> str:
    g = guard(args.get("browser"))
    if isinstance(g, str):
        return g
    key, info = g
    url = args.get("url", "")
    if not re.match(r"^https?://", url, re.I):
        return "只接受 http/https 网址。"
    ok, out = run_osa(script_open(info["app"], key != "safari", url))
    return ("已在 %s 打开：%s" % (info["app"], url)) if ok else ("打开失败：" + out)


HANDLERS = {
    "list_tabs": tool_list_tabs,
    "current_tab": tool_current_tab,
    "get_text": tool_get_text,
    "open_url": tool_open_url,
}


def reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply_error(msg_id, code, message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                 "error": {"code": code, "message": message}},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            reply(msg_id, {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion")
                                   or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "browser-bridge", "version": "0.2.0"},
            })
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            handler = HANDLERS.get(name)
            if not handler:
                reply(msg_id, {"content": [{"type": "text",
                                            "text": "未知工具：%s" % name}],
                               "isError": True})
                continue
            try:
                reply(msg_id, {"content": [{"type": "text", "text": handler(args)}]})
            except Exception as exc:  # noqa: BLE001
                reply(msg_id, {"content": [{"type": "text", "text": "调用失败：" + str(exc)}],
                               "isError": True})
        elif method == "ping":
            reply(msg_id, {})
        elif msg_id is not None:
            reply_error(msg_id, -32601, "unknown method: %s" % method)


if __name__ == "__main__":
    main()
