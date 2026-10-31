#!/usr/bin/env python3
"""browser_probe.py — 免安装的 MCP 浏览器驱动。

不需要 DSH 插件、不需要 npm 全局安装：自己发现 node 和 @playwright/mcp，
用 MCP stdio 协议拉起一个有头浏览器，导航、取快照、截图，并且可以在
「需要登录」时停下来等人类登录，登录完成后自动继续。

用法示例
--------
  # 打开页面并取可访问性快照
  python3 browser_probe.py --url https://example.com --snapshot

  # 撞到登录墙时停下来，最多等 10 分钟，登录完成后自动继续
  python3 browser_probe.py --url https://github.com/settings/profile \
      --wait-login 600 --snapshot

  # 只截个图
  python3 browser_probe.py --url https://example.com --screenshot out.png

  # 在页面里执行 JS
  python3 browser_probe.py --url https://example.com --eval "() => document.title"

依赖
----
  - node（本机已装，或 DSH 自带运行时里的那一个）
  - @playwright/mcp（首次运行可用 --npx 让 npx 自动拉取）
  - Chrome 或 Edge（默认用系统 Chrome，不会下载 Chromium）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

try:  # 输出被重定向时编码可能不是 UTF-8，会让中文变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DEFAULT_PROFILE = os.path.expanduser("~/.browser-handoff-profile")


# ---------------------------------------------------------------- 发现运行环境

def find_node() -> str | None:
    """按优先级找 node：环境变量 → DSH 运行时 → PATH → 常见安装位置。"""
    env = os.environ.get("NODE_BIN")
    if env and os.path.isfile(env):
        return env

    dsh_home = os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh")
    patterns = [
        os.path.join(dsh_home, "dsh-runtimes", "*", "dependencies", "node", "bin", "node"),
    ]
    for pat in patterns:
        for cand in sorted(glob.glob(pat)):
            if os.path.isfile(cand):
                return cand

    which = shutil.which("node")
    if which:
        return which

    for cand in ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"):
        if os.path.isfile(cand):
            return cand
    return None


def find_mcp_cli(explicit: str | None) -> str | None:
    """找 @playwright/mcp 的 cli.js。"""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    env = os.environ.get("PW_MCP_CLI")
    if env and os.path.isfile(env):
        return env

    patterns = [
        # DSH profile（用 install_dsh.py 装过就会有）
        os.path.expanduser("~/.dsh/profiles/*/node_modules/@playwright/mcp/cli.js"),
        # 本仓库自带的 .mcp-b
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "node_modules", "@playwright", "mcp", "cli.js"),
        # 全局 npm 安装
        "/usr/local/lib/node_modules/@playwright/mcp/cli.js",
        "/opt/homebrew/lib/node_modules/@playwright/mcp/cli.js",
    ]
    for pat in patterns:
        for cand in sorted(glob.glob(pat)):
            if os.path.isfile(cand):
                return os.path.realpath(cand)
    return None


# ---------------------------------------------------------------- MCP 客户端

class MCPClient:
    """最小 MCP stdio 客户端：一行一条 JSON-RPC 消息。"""

    def __init__(self, argv: list[str], env: dict[str, str], verbose: bool = False):
        self._id = 0
        self.err: list[str] = []
        self.verbose = verbose
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
        )
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        for line in self.proc.stderr:
            self.err.append(line.rstrip())
            if self.verbose:
                sys.stderr.write("[server] " + line)
            if len(self.err) > 400:
                del self.err[:200]

    def _send(self, obj: dict) -> None:
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                tail = "\n".join(self.err[-15:])
                raise SystemExit("MCP server 提前退出。stderr 末尾：\n" + tail)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise RuntimeError("MCP 错误：" +
                                   json.dumps(msg["error"], ensure_ascii=False))
            return msg.get("result") or {}

    def text_of(self, result: dict) -> str:
        parts = [i["text"] for i in result.get("content", [])
                 if i.get("type") == "text"]
        return "\n".join(parts)

    def call(self, name: str, arguments: dict | None = None) -> str:
        return self.text_of(self.request("tools/call", {
            "name": name, "arguments": arguments or {}}))

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------- 工具函数

DEFAULT_LOGIN_PROBE = (
    "() => { const m = document.querySelector('meta[name=user-login]');"
    " return JSON.stringify({url: location.href,"
    " pwd: !!document.querySelector('input[type=password]'),"
    " user: m ? m.content : null,"
    " title: (document.title || '').slice(0, 80)}); }"
)

WALL_URL_HINTS = ("/login", "/signin", "/sign_in", "/session", "/passport")

# 滚动整页、逐屏收集文本。适用于「正文在 DOM 里但按需渲染」的站点
# （典型例子：WPS/金山文档这类 canvas + 虚拟滚动的编辑器）。
# 注意：canvas 文字层往往没有空格，抽出来会是 "Thetransitiontowards..."。
EXTRACT_JS = r"""
async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const seen = new Set();
  const collect = () => {
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n; const parts = [];
    while ((n = w.nextNode())) {
      const p = n.parentElement;
      if (!p) continue;
      if (p.closest('style,script,noscript,template,svg')) continue;
      parts.push(n.nodeValue);
    }
    return parts.join('');
  };
  const store = raw => {
    for (const line of raw.split(/\n+/)) {
      const t = line.replace(/\s+/g, ' ').trim();
      if (t.length > 20) seen.add(t);
    }
  };
  store(collect());
  const max = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
  const step = Math.max(400, Math.floor(innerHeight * 0.8));
  for (let y = 0; y < max + step * 40; y += step) {
    window.scrollTo(0, y);
    await sleep(500);
    store(collect());
  }
  window.scrollTo(0, 0);
  return [...seen].join('\n');
}
"""


def result_text(text: str) -> str:
    """取工具返回里 ### Result 后面的原始内容（不解析 JSON）。"""
    marker = "### Result"
    idx = text.find(marker)
    body = (text[idx + len(marker):] if idx >= 0 else text).strip()
    end = body.find("\n###")
    if end >= 0:
        body = body[:end]
    return body.strip().strip("`").strip()


def parse_tool_json(text: str) -> dict:
    """从工具返回的 Markdown 里抠出 ### Result 后面的 JSON。"""
    body = result_text(text)
    try:
        value = json.loads(body)
    except Exception:
        return {}
    if isinstance(value, str):          # 页面函数返回的是 JSON 字符串
        try:
            value = json.loads(value)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def is_wall(info: dict) -> bool:
    if not info:
        return True
    if info.get("pwd"):
        return True
    url = (info.get("url") or "").lower()
    return any(h in url for h in WALL_URL_HINTS)


def home_is_writable() -> bool:
    """能不能往 ~/Library/Caches（或 ~/.cache）里写东西。"""
    cache = (os.path.join(os.path.expanduser("~"), "Library", "Caches")
             if sys.platform == "darwin"
             else os.path.join(os.path.expanduser("~"), ".cache"))
    try:
        os.makedirs(cache, exist_ok=True)
        probe = os.path.join(cache, ".browser-handoff-write-test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


def prepare_env(args) -> tuple[dict, str, bool]:
    """准备子进程环境；在受限沙盒里自动降级到可写目录。

    返回 (env, profile 目录, 是否检测到受限环境)。
    """
    env = dict(os.environ)
    env.setdefault("HOME", os.path.expanduser("~"))
    sandboxed = not home_is_writable()

    if sandboxed:
        fallback = os.path.join(os.path.abspath(args.output_dir), "_sandbox-home")
        try:
            os.makedirs(fallback, exist_ok=True)
        except OSError as exc:
            raise SystemExit(
                "无法创建缓存目录：%s\n错误：%s\n"
                "→ 换一个可写目录：--output-dir <路径>；或设置 HOME 到可写位置。"
                % (fallback, exc))
        env["HOME"] = fallback
        env["XDG_CACHE_HOME"] = fallback
        env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(fallback, "pw-browsers")
        print("[提示] 检测到受限环境（$HOME 不可写），已自动把缓存和 profile "
              "切换到输出目录，并启用 --no-sandbox。", file=sys.stderr)

    profile = args.profile or (
        os.path.join(os.path.abspath(args.output_dir), "_sandbox-profile")
        if sandboxed else DEFAULT_PROFILE)
    return env, profile, sandboxed


def build_argv(args, node: str, profile: str, no_sandbox: bool) -> list[str]:
    if args.npx:
        base = ["npx", "-y", "@playwright/mcp@latest"]
    else:
        cli = find_mcp_cli(args.cli)
        if not cli:
            raise SystemExit(
                "找不到 @playwright/mcp。三种办法任选其一：\n"
                "  1) 加 --npx 让 npx 现场拉取\n"
                "  2) 先跑：npm i -g @playwright/mcp\n"
                "  3) 用 --cli /path/to/@playwright/mcp/cli.js 指定\n"
                "（若已装 DSH，可先跑 install/install_dsh.py 装上）")
        base = [node, cli]

    base += ["--browser", args.browser,
             "--user-data-dir", profile,
             "--output-dir", args.output_dir]
    if args.headless:
        base.append("--headless")
    if no_sandbox or args.no_sandbox:
        base.append("--no-sandbox")
    if args.isolated:
        base.append("--isolated")
    return base


def main() -> None:
    p = argparse.ArgumentParser(
        description="免安装的 MCP 浏览器驱动（导航 / 快照 / 截图 / 等登录）")
    p.add_argument("--url", help="要打开的网址")
    p.add_argument("--snapshot", action="store_true", help="打印可访问性快照")
    p.add_argument("--screenshot", metavar="PATH", help="截图保存路径")
    p.add_argument("--extract", metavar="PATH",
                   help="自动滚动整页并抽取正文，保存为文本文件")
    p.add_argument("--eval", metavar="JS", help="执行 JS 函数，如 \"() => document.title\"")
    p.add_argument("--wait-login", type=int, metavar="SEC",
                   help="等待人类登录，最多等 SEC 秒；检测到已登录就继续")
    p.add_argument("--login-check", metavar="JS",
                   help="自定义登录检测函数，返回 true 表示尚未登录")
    p.add_argument("--interval", type=int, default=5, help="检测间隔秒数，默认 5")
    p.add_argument("--headed", dest="headless", action="store_false",
                   help="有头模式（默认）")
    p.add_argument("--headless", dest="headless", action="store_true",
                   help="无头模式")
    p.add_argument("--browser", default="chrome",
                   help="chrome 或 msedge，默认 chrome")
    p.add_argument("--profile", default=None,
                   help="持久化 profile 目录（默认 %s）" % DEFAULT_PROFILE)
    p.add_argument("--isolated", action="store_true",
                   help="用完即弃，不保留登录态")
    p.add_argument("--no-sandbox", action="store_true",
                   help="Chrome 起不来时加这个")
    p.add_argument("--output-dir", default=os.path.join(os.getcwd(), "browser-artifacts"),
                   help="截图/快照产物目录")
    p.add_argument("--self-test", action="store_true",
                   help="自检：起一个无头浏览器，验证 MCP 链路是否打通（装完先跑这个）")
    p.add_argument("--cli", help="手动指定 @playwright/mcp/cli.js")
    p.add_argument("--npx", action="store_true", help="用 npx 现场拉取")
    p.add_argument("--verbose", action="store_true", help="打印 server stderr")
    args = p.parse_args()

    if args.self_test and "--headed" not in sys.argv:
        args.headless = True          # 自检默认不弹窗
    self_test_tmp = None
    if args.self_test and "--output-dir" not in sys.argv:
        # 自检会拉起一个临时 Chrome profile（几十 MB），用完即删。
        # 优先用当前目录：受限环境里 TEMP 子目录可能不可写。
        for base in (os.getcwd(), tempfile.gettempdir()):
            try:
                d = os.path.join(base, "browser-handoff-selftest")
                os.makedirs(d, exist_ok=True)
                self_test_tmp = d
                break
            except OSError:
                continue
        if self_test_tmp:
            args.output_dir = self_test_tmp
            if not args.profile:
                args.profile = os.path.join(self_test_tmp, "profile")

    if not any([args.url, args.snapshot, args.screenshot, args.eval,
                args.wait_login, args.extract, args.self_test]):
        p.error("至少给一个动作：--url / --snapshot / --screenshot / --eval / "
                "--wait-login / --extract / --self-test")
    if args.wait_login and not args.url:
        p.error("--wait-login 必须配合 --url 使用，否则没有页面可等")

    os.makedirs(args.output_dir, exist_ok=True)
    env, profile, sandboxed = prepare_env(args)

    node = find_node()
    if not node and not args.npx:
        raise SystemExit("找不到 node。请安装 Node.js，或用 NODE_BIN=/path/to/node 指定。")
    argv = build_argv(args, node or "node", profile, sandboxed)

    if args.verbose:
        print("[argv] " + " ".join(argv), file=sys.stderr)

    client = MCPClient(argv, env, verbose=args.verbose)
    try:
        init = client.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "browser-probe", "version": "0.1.0"},
        })
        server = init.get("serverInfo") or {}
        if args.verbose:
            print("[init] %s v%s" % (server.get("name"), server.get("version")),
                  file=sys.stderr)
        client.notify("notifications/initialized")

        if args.self_test:
            steps = []
            tools = client.request("tools/list", {}).get("tools", [])
            steps.append(("工具列表", len(tools) > 15, "%d 个工具" % len(tools)))
            client.call("browser_navigate",
                        {"url": "data:text/html,<title>self-test</title>"
                                "<h1>self-test</h1>"})
            title = result_text(client.call(
                "browser_evaluate",
                {"function": "() => document.title + ' | ' + document.body.innerText"}))
            steps.append(("页面渲染", "self-test" in title, title[:40]))
            val = result_text(client.call("browser_evaluate",
                                          {"function": "() => 2 + 2"}))
            steps.append(("JS 执行", "4" in val, val[:20]))
            try:
                client.call("browser_close")
            except Exception:
                pass
            ok = all(s[1] for s in steps)
            print("自检结果：")
            for name, good, detail in steps:
                print("  %s %-6s %s" % ("✅" if good else "❌", name, detail))
            print("\n总体：", "通过，可以正常使用" if ok else "失败，请检查上面的错误")
            sys.exit(0 if ok else 1)

        if args.url:
            print(client.call("browser_navigate", {"url": args.url}))

        if args.wait_login:
            probe = args.login_check or DEFAULT_LOGIN_PROBE
            custom = bool(args.login_check)
            deadline = time.time() + args.wait_login
            stable = 0
            announced = False
            while time.time() < deadline:
                raw = client.call("browser_evaluate", {"function": probe})
                if custom:
                    # 自定义检测函数约定：返回 true 表示「仍未登录」
                    wall = result_text(raw).strip().lower().startswith("true")
                else:
                    wall = is_wall(parse_tool_json(raw))
                if wall:
                    stable = 0
                    if not announced:
                        print("\n>>> 需要登录：请在打开的浏览器窗口里完成登录，"
                              "我会自动检测并继续（最多等 %d 秒）。" % args.wait_login,
                              flush=True)
                        announced = True
                else:
                    stable += 1
                    if stable >= 2:
                        print("\n>>> 已登录，继续。", flush=True)
                        break
                time.sleep(args.interval)
            else:
                print("\n>>> 等待超时，仍未检测到登录完成。")
                return

        if args.eval:
            print(client.call("browser_evaluate", {"function": args.eval}))

        if args.snapshot:
            print(client.call("browser_snapshot"))

        if args.screenshot:
            path = os.path.abspath(args.screenshot)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            print(client.call("browser_take_screenshot",
                              {"filename": path, "scale": "css"}))

        if args.extract:
            raw = result_text(client.call("browser_evaluate", {"function": EXTRACT_JS}))
            try:                      # 工具返回的是 JSON 字符串，去掉外层引号
                unquoted = json.loads(raw)
                if isinstance(unquoted, str):
                    raw = unquoted
            except Exception:
                pass
            path = os.path.abspath(args.extract)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(raw)
            print("已抽取 %d 字符 -> %s" % (len(raw), path))

        client.call("browser_close")
    finally:
        client.close()
        if self_test_tmp:
            shutil.rmtree(self_test_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
