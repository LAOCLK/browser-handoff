#!/usr/bin/env python3
"""stdin_encoding_test.py — 验证中文在 MCP stdio 上不会被解码坏。

Windows 上 python 默认用控制台代码页（cp936）读 stdin。DSH 启动桥时不会设
PYTHONUTF8，所以请求里的中文参数（要填的搜索词、要打开的带中文 URL）
会在 stdin 上被解成乱码。这个脚本用同样的默认环境复现，并断言往返一致。

不需要浏览器：假扩展把收到的参数原样回传。
退出码：0 = 通过，1 = 中文被解坏（说明桥没把 stdin 切成 UTF-8）。
"""
from __future__ import annotations

import json
import os
import re
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
TEXT = "安全加固验收"


class FakeExt(threading.Thread):
    def __init__(self, base: str):
        super().__init__(daemon=True)
        self.base, self.stopped, self.got = base, False, None

    def run(self) -> None:
        while not self.stopped:
            try:
                with urllib.request.urlopen(self.base + "/poll?agent=edge",
                                            timeout=20) as r:
                    msg = json.loads(r.read().decode("utf-8"))
            except Exception:
                time.sleep(0.2)
                continue
            if "id" not in msg:
                continue
            args = msg["command"].get("args") or {}
            self.got = args.get("text")
            body = json.dumps({"id": msg["id"], "ok": True,
                               "data": {"value": args.get("text", "")}}).encode("utf-8")
            req = urllib.request.Request(self.base + "/result", data=body,
                                         headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:
                pass


def main() -> int:
    if not os.path.isfile(SERVER):
        print("找不到 %s" % SERVER)
        return 1

    # 模拟 DSH 的默认环境：去掉所有「强制 UTF-8」的变量
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("PYTHONUTF8", "PYTHONIOENCODING",
                                "PYTHONLEGACYWINDOWSSTDIO")}
    # 测试进程没有 token.txt，用显式逃生口，避免被「失败关闭」拦住
    env["BROWSER_BRIDGE_ALLOW_INSECURE"] = "1"
    proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", bufsize=1, env=env)
    base = None
    end = time.time() + 15
    while time.time() < end:
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.05)
            continue
        m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
        if m:
            base = "http://127.0.0.1:" + m.group(1)
            break
    if not base:
        proc.kill()
        print("等不到桥的监听端口")
        return 1

    ext = FakeExt(base)
    ext.start()
    time.sleep(0.4)
    try:
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "type",
                          "arguments": {"browser": "edge", "ref": "e1",
                                        "text": TEXT}}}
        proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        proc.stdout.readline()
    finally:
        proc.kill()
        ext.stopped = True

    ok = ext.got == TEXT
    print("  %s 中文往返       发送=%s 收到=%s"
          % ("✅" if ok else "❌", TEXT, ext.got))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
