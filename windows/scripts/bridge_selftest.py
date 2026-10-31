#!/usr/bin/env python3
"""bridge_selftest.py — 不启动浏览器，验证 Windows 桥的完整协议。

做法：拉起 servers/browser_bridge_win.py，再在进程内扮演一个「扩展」，
按 /poll 取命令、按 /result 回结果，然后用 MCP 客户端调四个工具，
断言返回结构正确。

用途：
  * 开发时回归（改完桥跑一下就知道有没有断）
  * 用户手动排查：`python scripts\\bridge_selftest.py`
  * smoke_test.py 会调用它

退出码：0 = 通过，1 = 失败。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "servers", "browser_bridge_win.py")

FAKE_TABS = [
    {"window": 1, "tab": 1, "title": "自检页面 A", "url": "https://example.com/a"},
    {"window": 1, "tab": 2, "title": "自检页面 B", "url": "https://example.com/b"},
]


class Extension(threading.Thread):
    """假装自己是浏览器扩展：长轮询取命令，回固定结果。"""

    def __init__(self, base: str, agent: str = "edge"):
        super().__init__(daemon=True)
        self.base = base
        self.agent = agent
        self.stopped = False
        self.seen: list[str] = []

    def _get(self, path: str, timeout: float = 20.0):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path: str, obj: dict) -> None:
        data = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()

    def answer(self, tool: str, args: dict) -> dict:
        self.seen.append(tool)
        if tool == "list_tabs":
            return {"ok": True, "data": {"tabs": FAKE_TABS}}
        if tool == "current_tab":
            return {"ok": True, "data": {"title": FAKE_TABS[0]["title"],
                                         "url": FAKE_TABS[0]["url"]}}
        if tool == "get_text":
            return {"ok": True, "data": {"text": "第一段正文。\n\n\n第二段正文。"}}
        if tool == "snapshot":
            return {"ok": True, "data": {
                "url": "https://example.com/a", "title": "自检页面 A",
                "count": 2,
                "tree": '- button "提交" [ref=e1]\n- textbox "搜索" [ref=e2]'}}
        if tool == "click":
            return {"ok": True, "data": {"clicked": {"tag": "button", "text": "提交"}}}
        if tool == "type":
            return {"ok": True, "data": {"value": args.get("text", "")}}
        if tool == "open_url":
            return {"ok": True, "data": {}}
        return {"ok": False, "error": "unexpected tool"}

    def run(self) -> None:
        while not self.stopped:
            try:
                msg = self._get("/poll?agent=" + self.agent + "&selftest=1", timeout=20)
            except Exception:
                time.sleep(0.2)
                continue
            if "id" not in msg or "command" not in msg:
                continue
            cmd = msg["command"]
            res = self.answer(cmd.get("tool", ""), cmd.get("args") or {})
            try:
                self._post("/result", {"id": msg["id"], **res})
            except Exception:
                pass


class Client:
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.seq = 0

    def call(self, method: str, params: dict | None = None):
        self.seq += 1
        req = {"jsonrpc": "2.0", "id": self.seq, "method": method,
               "params": params or {}}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("桥进程提前退出")
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == self.seq:
                return msg

    def tool(self, name: str, args: dict | None = None) -> str:
        msg = self.call("tools/call", {"name": name, "arguments": args or {}})
        content = msg.get("result", {}).get("content") or []
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")


def read_port(proc: subprocess.Popen, deadline: float = 20.0) -> str:
    import re
    end = time.time() + deadline
    while time.time() < end:
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.05)
            continue
        m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
        if m:
            return "http://127.0.0.1:" + m.group(1)
        if "无法在" in line:
            raise RuntimeError(line.strip())
    raise RuntimeError("等不到桥的监听端口")


def main() -> int:
    if not os.path.isfile(SERVER):
        print("找不到 %s" % SERVER)
        return 1

    proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1,
                            env={**os.environ, "BROWSER_BRIDGE_PORT": "",
                                 "BROWSER_BRIDGE_SELFTEST": "1"})
    checks: list[tuple[str, bool, str]] = []
    try:
        base = read_port(proc)
        ext = Extension(base)
        ext.start()
        time.sleep(0.4)           # 让扩展先完成一次 hello
        cli = Client(proc)

        init = cli.call("initialize", {"protocolVersion": "2025-06-18"})
        ok = init.get("result", {}).get("serverInfo", {}).get("name") == "browser-bridge-win"
        checks.append(("initialize", ok, str(init.get("result", {}).get("serverInfo"))))

        tools = cli.call("tools/list").get("result", {}).get("tools", [])
        names = sorted(t.get("name") for t in tools)
        checks.append(("tools/list", names == ["click", "current_tab", "get_text",
                                               "list_tabs", "open_url", "snapshot",
                                               "type"], ",".join(names)))

        out = cli.tool("list_tabs", {"browser": "edge"})
        ok = all(t in out for t in ("自检页面 A", "https://example.com/b", '"count": 2'))
        checks.append(("list_tabs", ok, out[:80].replace("\n", " ")))

        out = cli.tool("current_tab", {"browser": "edge"})
        ok = "https://example.com/a" in out and "自检页面 A" in out
        checks.append(("current_tab", ok, out[:80].replace("\n", " ")))

        out = cli.tool("get_text", {"browser": "edge"})
        ok = "第一段正文" in out and "第二段正文" in out and "\n\n\n" not in out
        checks.append(("get_text", ok, out[:60].replace("\n", "⏎")))

        out = cli.tool("open_url", {"browser": "edge", "url": "https://example.com/x"})
        ok = "已在 Microsoft Edge 打开" in out
        checks.append(("open_url", ok, out[:80]))

        out = cli.tool("snapshot", {"browser": "edge"})
        ok = "[ref=e1]" in out and "可交互元素" in out and "自检页面 A" in out
        checks.append(("snapshot", ok, out[:60].replace("\n", "⏎")))

        out = cli.tool("click", {"browser": "edge", "ref": "e1"})
        ok = "已点击" in out and "ref=e1" in out
        checks.append(("click", ok, out[:60]))

        out = cli.tool("click", {"browser": "edge", "ref": "bogus"})
        ok = "ref 形如 e3" in out
        checks.append(("click 拒绝非法 ref", ok, out[:40]))

        out = cli.tool("type", {"browser": "edge", "ref": "e2", "text": "hello"})
        ok = "已向 ref=e2 输入 5 个字符" in out
        checks.append(("type", ok, out[:60]))

        out = cli.tool("open_url", {"browser": "edge", "url": "ftp://x"})
        ok = "只接受 http/https" in out
        checks.append(("open_url 拒绝非法协议", ok, out[:40]))

        out = cli.tool("list_tabs", {"browser": "chrome"})
        ok = "扩展没有连接" in out
        checks.append(("未连接的浏览器有明确提示", ok, out[:60].replace("\n", " ")))

        ext.stopped = True
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    failed = 0
    for name, ok, detail in checks:
        if not ok:
            failed += 1
        print("  %s %-26s %s" % ("✅" if ok else "❌", name, detail))
    print("\n结果：", "桥协议全部通过" if not failed else "%d 项失败" % failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
