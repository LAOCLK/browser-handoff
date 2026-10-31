#!/usr/bin/env python
"""browser_bridge_win.py — 「读取你正在用的浏览器」MCP 服务（Windows）。

提供四个工具：

    list_tabs     列出某浏览器所有窗口/标签页（标题 + URL）
    current_tab   当前窗口的活动标签页（标题 + URL）
    get_text      当前标签页正文纯文本
    open_url      在该浏览器新标签页打开网址

实现方式：浏览器扩展 + 本地长轮询。
扩展装在 Edge/Chrome 里，用你已经登录的 profile，通过 127.0.0.1 上的
HTTP 长轮询接收命令，用 chrome.tabs / chrome.scripting 执行后回传结果。

进程模型：
    DSH 宿主进程（不受文件沙盒约束）
      └─ python browser_bridge_win.py            ← 本文件，MCP stdio
           └─ ThreadingHTTPServer 127.0.0.1:8790-8799
                 ▲ 长轮询 /poll       │ POST /result
                 └── 扩展 service worker（Edge / Chrome）

只监听回环地址；除本机浏览器扩展外没有网络暴露面。
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    # Windows 上 stdio 默认走控制台代码页（cp936）：中文会变成乱码。
    # 三个流都要切 UTF-8 —— 尤其 stdin，MCP 请求里的中文参数从这里进来。
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

VERSION = "1.0.0"
PORT_RANGE = range(8790, 8800)
POLL_WAIT = 15.0          # 单次长轮询最多挂多久（秒）
PICKUP_TIMEOUT = 45.0     # 命令等扩展接走 + 执行完的总时长
                          # （MV3 的 service worker 被休眠时，最坏要等闹钟把它唤醒）
MAX_TEXT = 20000
STALE_AFTER = 90.0        # 多久没轮询算「扩展没连上」

BROWSER_NAMES = {"edge": "Microsoft Edge", "chrome": "Google Chrome"}

# bridge_selftest.py 用它把真实扩展挡在门外：真实扩展不会带 selftest=1，
# 否则它可能连上自检的临时桥、抢走本该由假扩展回答的命令。
SELFTEST = os.environ.get("BROWSER_BRIDGE_SELFTEST") == "1"

# 只给开发/自检用的显式逃生口。默认情况下「没有令牌」是**致命错误**，
# 不静默降级 —— 否则删掉 token.txt 就能把鉴权悄悄关掉。
ALLOW_INSECURE = os.environ.get("BROWSER_BRIDGE_ALLOW_INSECURE") == "1"

MIN_TOKEN_LEN = 16


def load_token() -> str:
    """共享令牌：环境变量优先，其次同目录 token.txt（安装器写入）。

    返回空串表示「没有可用的令牌」；调用方负责拒绝启动，而不是降级。
    """
    env = (os.environ.get("BROWSER_BRIDGE_TOKEN") or "").strip()
    if env:
        return env
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


TOKEN = load_token()
if TOKEN and len(TOKEN) < MIN_TOKEN_LEN:
    TOKEN = ""          # 文件被截断/写坏，按「没有令牌」处理，不静默放过

TOOLS = [
    {
        "name": "list_tabs",
        "description": ("列出某个浏览器里所有窗口和标签页（标题 + URL）。"
                        "browser 可选 edge / chrome，不填则用已连接的浏览器。"),
        "inputSchema": {
            "type": "object",
            "properties": {"browser": {"type": "string",
                                       "enum": ["edge", "chrome"]}},
        },
    },
    {
        "name": "current_tab",
        "description": "返回该浏览器最前面窗口的当前标签页标题和 URL。",
        "inputSchema": {
            "type": "object",
            "properties": {"browser": {"type": "string",
                                       "enum": ["edge", "chrome"]}},
        },
    },
    {
        "name": "get_text",
        "description": ("读取该浏览器当前标签页的正文纯文本。"
                        "需要对应的浏览器装了 browser-handoff 扩展。"),
        "inputSchema": {
            "type": "object",
            "properties": {"browser": {"type": "string",
                                       "enum": ["edge", "chrome"]}},
        },
    },
    {
        "name": "snapshot",
        "description": ("对该浏览器当前标签页做一次可交互元素快照，返回带 ref 的列表"
                        "（按钮/链接/输入框等）。后续 click / type 用这些 ref。"
                        "页面变化后 ref 会失效，需要重新 snapshot。"),
        "inputSchema": {
            "type": "object",
            "properties": {"browser": {"type": "string",
                                       "enum": ["edge", "chrome"]}},
        },
    },
    {
        "name": "click",
        "description": ("点击当前标签页里的元素。ref 来自最近一次 snapshot，"
                        "形如 e3。会先滚动到可见再点击。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "browser": {"type": "string", "enum": ["edge", "chrome"]},
                "ref": {"type": "string", "description": "snapshot 返回的 ref，如 e3"},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "type",
        "description": ("在输入框里填写文字（input / textarea / contenteditable）。"
                        "ref 来自最近一次 snapshot。submit=true 时额外按回车/提交表单。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "browser": {"type": "string", "enum": ["edge", "chrome"]},
                "ref": {"type": "string", "description": "snapshot 返回的 ref，如 e3"},
                "text": {"type": "string", "description": "要填入的文字"},
                "submit": {"type": "boolean", "description": "填完是否提交"},
            },
            "required": ["ref", "text"],
        },
    },
    {
        "name": "open_url",
        "description": ("在该浏览器新建标签页打开网址并把窗口提到前台。只接受 http/https。"
                        "遇到登录、付费、人机验证这类必须由人过的页面时，用它把页面交给用户，"
                        "然后停下等回复，不要自己换来源硬绕。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "browser": {"type": "string",
                            "enum": ["edge", "chrome"]},
                "url": {"type": "string", "description": "要打开的网址"},
            },
            "required": ["url"],
        },
    },
]


# --------------------------------------------------------------------- 状态

class Bridge:
    """扩展连接状态 + 命令队列。"""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.queues: dict[str, queue.Queue] = {}
        self.pending: dict[str, dict] = {}
        self.last_seen: dict[str, float] = {}

    # -- 扩展侧 ------------------------------------------------------

    def touch(self, agent: str) -> None:
        with self.cond:
            self.last_seen[agent] = time.time()
            self.queues.setdefault(agent, queue.Queue())
            self.cond.notify_all()

    def pop_command(self, agent: str, wait: float) -> dict | None:
        """长轮询：有命令就取走，没有就挂到 wait 秒。"""
        self.touch(agent)
        q = self.queues.setdefault(agent, queue.Queue())
        deadline = time.time() + wait
        with self.cond:
            while True:
                try:
                    return q.get_nowait()
                except queue.Empty:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return None
                    self.cond.wait(min(remaining, 1.0))

    def deliver(self, msg_id: int, ok: bool, data, error: str | None) -> None:
        with self.cond:
            slot = self.pending.get(msg_id)
            if not slot:
                return
            slot["ok"], slot["data"], slot["error"] = ok, data, error
            slot["event"].set()
            self.cond.notify_all()

    # -- 工具侧 ------------------------------------------------------

    def connected(self) -> list[str]:
        now = time.time()
        with self.cond:
            return sorted(a for a, t in self.last_seen.items()
                          if now - t <= STALE_AFTER)

    def pick_agent(self, browser: str | None) -> tuple[str | None, str | None]:
        """返回 (agent, 错误文本)。"""
        live = self.connected()
        if browser:
            key = browser.lower()
            if key not in BROWSER_NAMES:
                return None, ("不支持的浏览器：%s（Windows 上可选 edge / chrome）"
                              % browser)
            if key not in live:
                return None, self._not_connected(key, live)
            return key, None
        if not live:
            return None, self._no_extension()
        # 没指定：两边都连着时优先 Edge（Windows 默认浏览器居多）
        return ("edge" if "edge" in live else live[0]), None

    def call(self, agent: str, tool: str, args: dict,
             timeout: float = PICKUP_TIMEOUT) -> tuple[bool, object, str | None]:
        with self.cond:
            # 命令 id 用随机串：可预测的自增整数会让本机网页有机会盲猜
            # 并伪造 /result（prompt injection 的入口）。
            msg_id = uuid.uuid4().hex
            slot = {"event": threading.Event(), "ok": None,
                    "data": None, "error": None}
            self.pending[msg_id] = slot
            self.queues.setdefault(agent, queue.Queue()).put(
                {"id": msg_id, "tool": tool, "args": args})
            self.cond.notify_all()

        slot["event"].wait(timeout)

        with self.cond:
            self.pending.pop(msg_id, None)

        if not slot["event"].is_set():
            return False, None, "%s 没有响应（扩展可能被浏览器休眠了，稍后重试）。" % (
                BROWSER_NAMES.get(agent, agent))
        return bool(slot["ok"]), slot["data"], slot["error"]

    # -- 提示文本 ----------------------------------------------------

    def _no_extension(self) -> str:
        hint = ""
        if TOKEN:
            hint = ("\n如果你刚升级过：新版要求扩展带上令牌，请在 edge://extensions 里"
                    "点一下本扩展的「重新加载」。")
        return ("没有检测到任何已连接的浏览器扩展。\n"
                "请在该浏览器里加载 browser-handoff 扩展：打开 edge://extensions "
                "或 chrome://extensions → 打开「开发人员模式」→「加载解压缩的扩展」"
                "→ 选择 %USERPROFILE%\\.dsh\\browser-bridge\\extension。\n"
                "装好后本工具立刻可用，不需要重启 DSH。" + hint)

    def _not_connected(self, key: str, live: list[str]) -> str:
        name = BROWSER_NAMES.get(key, key)
        if live:
            return ("%s 的扩展没有连接（当前已连接：%s）。请确认该浏览器正在运行，"
                    "且 %s 里装了 browser-handoff 扩展。"
                    % (name, "、".join(BROWSER_NAMES.get(a, a) for a in live), name))
        return self._no_extension()


BRIDGE = Bridge()


# --------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "browser-handoff-bridge/" + VERSION

    def log_message(self, fmt, *args):  # 不污染 stderr
        pass

    # -- helpers -----------------------------------------------------

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _agent(self, params: dict) -> str:
        raw = (params.get("agent") or ["edge"])[0].strip().lower()
        return raw if raw in BROWSER_NAMES else "edge"

    # -- 鉴权 --------------------------------------------------------

    def _deny(self, why: str) -> None:
        self._json(403, {"ok": False, "error": "forbidden: " + why})

    def _authorized(self, params: dict | None = None) -> bool:
        """Host / Origin / token 三道校验。

        回环地址不等于安全边界：同一台机器上的任意进程、以及任意网页，
        都能向 127.0.0.1 发请求。这里挡的是「不是本扩展」的调用方。
        """
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if host not in ("127.0.0.1", "localhost", ""):
            self._deny("bad host")
            return False

        origin = self.headers.get("Origin")
        if origin and not origin.startswith("chrome-extension://"):
            self._deny("bad origin")
            return False

        if TOKEN:
            if params is None:
                got = self.headers.get("X-Bridge-Token") or ""
            else:
                got = (params.get("token") or [""])[0]
            if got != TOKEN:
                self._deny("bad token")
                return False
        return True

    # -- routes ------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            if SELFTEST and params.get("selftest") != ["1"]:
                return self._json(403, {"ok": False, "error": "selftest only"})

            if parsed.path not in ("/hello", "/poll", "/status"):
                return self._json(404, {"ok": False, "error": "unknown path"})

            # /status 只给本机诊断用（不带命令、不带页面内容），同样要求 token
            if not self._authorized(params):
                return

            if parsed.path == "/hello":
                agent = self._agent(params)
                BRIDGE.touch(agent)
                return self._json(200, {"ok": True, "name": "browser-handoff",
                                        "version": VERSION, "agent": agent})

            if parsed.path == "/poll":
                agent = self._agent(params)
                cmd = BRIDGE.pop_command(agent, POLL_WAIT)
                if cmd is None:
                    return self._json(200, {"idle": True})
                return self._json(200, {"id": cmd["id"], "command": {
                    "tool": cmd["tool"], "args": cmd["args"]}})

            return self._json(200, {"ok": True, "version": VERSION,
                                    "connected": BRIDGE.connected(),
                                    "port": self.server.server_address[1],
                                    "auth": bool(TOKEN)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            try:
                return self._json(500, {"ok": False, "error": "internal"})
            except Exception:  # noqa: BLE001
                return

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/result":
                return self._json(404, {"ok": False, "error": "unknown path"})
            if not self._authorized(None):
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            msg = json.loads(raw.decode("utf-8") or "{}")
            msg_id = msg.get("id")
            # 只接受仍在 pending 里的随机 id：伪造/过期的提交一律丢弃
            if isinstance(msg_id, str) and msg_id:
                BRIDGE.deliver(msg_id, bool(msg.get("ok")),
                               msg.get("data"), msg.get("error"))
            return self._json(200, {"ok": True})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            try:
                return self._json(500, {"ok": False, "error": "internal"})
            except Exception:  # noqa: BLE001
                return


class BridgeServer(ThreadingHTTPServer):
    """Windows 上 SO_REUSEADDR 会让两个进程同时绑同一端口，必须关掉。"""

    allow_reuse_address = False
    allow_reuse_port = False
    daemon_threads = True


def start_http() -> ThreadingHTTPServer:
    forced = os.environ.get("BROWSER_BRIDGE_PORT")
    ports = [int(forced)] if forced and forced.isdigit() else list(PORT_RANGE)
    last: Exception | None = None
    for port in ports:
        try:
            srv = BridgeServer(("127.0.0.1", port), Handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            print("[bridge] listening on http://127.0.0.1:%d" % port,
                  file=sys.stderr, flush=True)
            return srv
        except OSError as exc:  # 端口占用
            last = exc
            continue
    raise SystemExit("无法在 %s 上监听：%s" % (list(PORT_RANGE), last))


# --------------------------------------------------------------------- 工具

def _text_fail(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}]}


def tool_list_tabs(args: dict) -> dict:
    agent, err = BRIDGE.pick_agent(args.get("browser"))
    if err:
        return _text_fail(err)
    ok, data, error = BRIDGE.call(agent, "list_tabs", {})
    if not ok:
        return _text_fail("读取失败：" + (error or "未知错误"))
    tabs = (data or {}).get("tabs") or []
    if not tabs:
        return _text_fail("%s 没有打开的标签页。" % BROWSER_NAMES[agent])
    return _text_fail(json.dumps({"browser": agent, "count": len(tabs),
                                  "tabs": tabs}, ensure_ascii=False))


def tool_current_tab(args: dict) -> dict:
    agent, err = BRIDGE.pick_agent(args.get("browser"))
    if err:
        return _text_fail(err)
    ok, data, error = BRIDGE.call(agent, "current_tab", {})
    if not ok:
        return _text_fail("读取失败：" + (error or "未知错误"))
    info = data or {}
    if not info.get("url") and not info.get("title"):
        return _text_fail("%s 没有打开的窗口。" % BROWSER_NAMES[agent])
    return _text_fail(json.dumps({"browser": agent,
                                  "title": info.get("title") or "",
                                  "url": info.get("url") or ""},
                                 ensure_ascii=False))


def tool_get_text(args: dict) -> dict:
    agent, err = BRIDGE.pick_agent(args.get("browser"))
    if err:
        return _text_fail(err)

    # 刚 open_url 或刚切标签时页面可能还没渲染完，读出来是空串。
    # 重试几次再下结论，避免把「还在加载」误报成「没有正文」。
    text = ""
    error = None
    for attempt in range(3):
        ok, data, error = BRIDGE.call(agent, "get_text", {})
        if not ok:
            return _text_fail("读取失败：" + (error or "未知错误"))
        text = (data or {}).get("text") or ""
        if text.strip():
            break
        if attempt < 2:
            time.sleep(0.8)

    if not text.strip():
        return _text_fail("%s 当前页面没有可读文本（可能还在加载，稍后重试）。"
                          % BROWSER_NAMES[agent])
    text = re.sub(r"\n{3,}", "\n\n", text)
    return _text_fail(text[:MAX_TEXT])


def tool_open_url(args: dict) -> dict:
    agent, err = BRIDGE.pick_agent(args.get("browser"))
    if err:
        return _text_fail(err)
    url = args.get("url", "")
    if not re.match(r"^https?://", url, re.I):
        return _text_fail("只接受 http/https 网址。")
    ok, _data, error = BRIDGE.call(agent, "open_url", {"url": url})
    if not ok:
        return _text_fail("打开失败：" + (error or "未知错误"))
    return _text_fail("已在 %s 打开：%s" % (BROWSER_NAMES[agent], url))


def tool_snapshot(args: dict) -> dict:
    agent, err = BRIDGE.pick_agent(args.get("browser"))
    if err:
        return _text_fail(err)
    ok, data, error = BRIDGE.call(agent, "snapshot", {})
    if not ok:
        return _text_fail("快照失败：" + (error or "未知错误"))
    info = data or {}
    tree = (info.get("tree") or "").strip()
    head = "URL: %s\nTitle: %s" % (info.get("url") or "", info.get("title") or "")
    if not tree:
        return _text_fail(head + "\n\n（没有找到可交互元素）")
    return _text_fail("%s\n\n%d 个可交互元素：\n%s"
                      % (head, int(info.get("count") or 0), tree[:MAX_TEXT]))


def tool_click(args: dict) -> dict:
    agent, err = BRIDGE.pick_agent(args.get("browser"))
    if err:
        return _text_fail(err)
    ref = str(args.get("ref") or "").strip()
    if not re.match(r"^e\d+$", ref):
        return _text_fail("ref 形如 e3，请先调用 snapshot 拿到。")
    ok, data, error = BRIDGE.call(agent, "click", {"ref": ref})
    if not ok:
        return _text_fail("点击失败：" + (error or "未知错误"))
    info = (data or {}).get("clicked") or {}
    label = info.get("text") or ""
    suffix = (' "%s"' % label) if label else ""
    return _text_fail("已点击 %s%s（ref=%s）"
                      % (info.get("tag") or "元素", suffix, ref))


def tool_type(args: dict) -> dict:
    agent, err = BRIDGE.pick_agent(args.get("browser"))
    if err:
        return _text_fail(err)
    ref = str(args.get("ref") or "").strip()
    if not re.match(r"^e\d+$", ref):
        return _text_fail("ref 形如 e3，请先调用 snapshot 拿到。")
    text = args.get("text")
    if text is None:
        return _text_fail("缺少 text 参数。")
    ok, data, error = BRIDGE.call(agent, "type", {
        "ref": ref, "text": str(text), "submit": bool(args.get("submit")),
    })
    if not ok:
        return _text_fail("输入失败：" + (error or "未知错误"))
    value = (data or {}).get("value")
    tail = ("，当前值：%s" % value) if value not in (None, "") else ""
    return _text_fail("已向 ref=%s 输入 %d 个字符%s%s"
                      % (ref, len(str(text)),
                         "并提交" if args.get("submit") else "", tail))


HANDLERS = {
    "list_tabs": tool_list_tabs,
    "current_tab": tool_current_tab,
    "get_text": tool_get_text,
    "snapshot": tool_snapshot,
    "click": tool_click,
    "type": tool_type,
    "open_url": tool_open_url,
}


# --------------------------------------------------------------------- MCP

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
    # 失败关闭：没有令牌就不启动，绝不静默降级成无鉴权。
    if not TOKEN:
        if SELFTEST or ALLOW_INSECURE:
            print("[bridge] 警告：无令牌模式（%s），仅限开发/自检。"
                  % ("selftest" if SELFTEST else "BROWSER_BRIDGE_ALLOW_INSECURE=1"),
                  file=sys.stderr, flush=True)
        else:
            print("[bridge] 找不到有效的令牌（token.txt 缺失或长度不足 %d），"
                  "拒绝以无鉴权模式启动。" % MIN_TOKEN_LEN, file=sys.stderr, flush=True)
            print("[bridge] 请重跑 install/install_dsh.py 生成令牌；"
                  "开发场景可设 BROWSER_BRIDGE_ALLOW_INSECURE=1。",
                  file=sys.stderr, flush=True)
            raise SystemExit(2)

    start_http()
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
                "serverInfo": {"name": "browser-bridge-win", "version": VERSION},
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
                reply(msg_id, handler(args))
            except Exception as exc:  # noqa: BLE001
                reply(msg_id, {"content": [{"type": "text",
                                            "text": "调用失败：" + str(exc)}],
                               "isError": True})
        elif method == "ping":
            reply(msg_id, {})
        elif msg_id is not None:
            reply_error(msg_id, -32601, "unknown method: %s" % method)


if __name__ == "__main__":
    main()
