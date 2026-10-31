#!/usr/bin/env python
"""smoke_test.py — 一条命令跑完包里所有能本地验证的项。

用法：
    python scripts/smoke_test.py          # 全部检查
    python scripts/smoke_test.py --quick  # 跳过浏览器自检（约省 10 秒）
    python scripts/smoke_test.py --browser msedge  # 用 Edge 跑自检

退出码：0 = 全绿，1 = 有硬性失败。
"""

from __future__ import annotations
import glob
import importlib.util
import os
import subprocess
import sys

try:  # Windows 上输出被重定向时默认用 GBK，会让中文变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PY_FILES = [
    os.path.join(ROOT, "install", "install_dsh.py"),
    *sorted(glob.glob(os.path.join(ROOT, "scripts", "*.py"))),
    os.path.join(ROOT, "servers", "browser_bridge_win.py"),
]


def run(cmd, timeout=180):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def check_compile():
    # 在内存里 compile，不写 .pyc —— 沙盒/只读环境下也不会因写缓存失败
    code = ("import sys\n"
            "for p in sys.argv[1:]:\n"
            "    compile(open(p, encoding='utf-8').read(), p, 'exec')\n")
    r = run([sys.executable, "-c", code, *PY_FILES])
    detail = (r.stderr or "").strip().splitlines()
    return r.returncode == 0, (detail[-1][:120] if detail else "%d 个文件" % len(PY_FILES))


def check_blocks():
    spec = importlib.util.spec_from_file_location(
        "inst", os.path.join(ROOT, "install", "install_dsh.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    node = "C:/Users/x/.dsh/dsh-runtimes/node.exe"
    home = "C:/Users/x"
    prof = "C:/Users/x/.dsh/profiles/desktop"
    blocks = {
        "playwright": mod.build_block(node, prof, home),
        "browser": mod.win_bridge_block("C:/Users/x/.dsh/python/python.exe", home),
        "edge": mod.edge_block(node, prof, home),
    }
    need = {
        "playwright": ["serverName: playwright", "--browser", "chrome"],
        "browser": ["serverName: browser", "browser_bridge_win.py"],
        "edge": ["serverName: edge", "msedge"],
    }
    bad = []
    for name, text in blocks.items():
        for token in need[name]:
            if token not in text:
                bad.append("%s 缺少 %s" % (name, token))
    return not bad, "; ".join(bad) if bad else "3 份配置块齐全"


def browser_arg():
    """--browser chrome|msedge，默认 chrome。"""
    for i, a in enumerate(sys.argv):
        if a == "--browser" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--browser="):
            return a.split("=", 1)[1]
    return "chrome"


def check_self_test():
    r = run([sys.executable, os.path.join(HERE, "browser_probe.py"),
             "--self-test", "--browser", browser_arg()])
    ok = r.returncode == 0 and "通过" in (r.stdout or "")
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return ok, ("[%s] " % browser_arg()) + (tail[-1][:100] if tail else "(无输出)")


def check_bridge():
    """桥协议自检：不需要浏览器，只验证服务端 + 协议契约。"""
    r = run([sys.executable, os.path.join(HERE, "bridge_selftest.py")], timeout=120)
    ok = r.returncode == 0
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return ok, (tail[-1][:100] if tail else "(无输出)")


def check_stdin_encoding():
    """中文经 MCP stdio 往返不变形（DSH 默认环境是 cp936，最容易坏在这里）。"""
    r = run([sys.executable, os.path.join(HERE, "stdin_encoding_test.py")], timeout=120)
    ok = r.returncode == 0
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return ok, (tail[-1][:100] if tail else "(无输出)")


def check_install_state():
    """只报告，不计入失败——没装的人也应该能跑冒烟测试。"""
    r = run([sys.executable,
             os.path.join(ROOT, "install", "install_dsh.py"), "--check"])
    line = ""
    for ln in (r.stdout or "").splitlines():
        if "主通道" in ln or "插件已装版本" in ln or "桥 (browser)" in ln:
            line += ln.strip() + "；"
    return True, (line or "未安装（可选）")[:180]


def main():
    quick = "--quick" in sys.argv
    checks = [("Python 编译", check_compile),
              ("配置块生成", check_blocks),
              ("桥协议", check_bridge),
              ("中文往返", check_stdin_encoding)]
    if not quick:
        checks.append(("%s 自检" % browser_arg(), check_self_test))
    checks.append(("本机安装状态", check_install_state))

    print("browser-handoff 冒烟测试\n" + "=" * 46)
    failed = 0
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "异常：%s" % exc
        if not ok:
            failed += 1
        print("  %s %-10s %s" % ("✅" if ok else "❌", name, detail))

    print("=" * 46)
    print("结果：", "全部通过" if failed == 0 else "%d 项失败" % failed)

    # 编译检查会留下 __pycache__，别让它污染包
    for dirpath, dirnames, _ in os.walk(ROOT):
        if "__pycache__" in dirnames:
            import shutil
            shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
