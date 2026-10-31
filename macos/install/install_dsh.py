#!/usr/bin/env python3
"""install_dsh.py — 给 DeepSeek Harness 装上 Playwright MCP 浏览器工具。

做的事只有两件（都可回滚）：
  1. 往当前 profile 里安装 @deepseek-ai/dsh-mcp-client 和 @playwright/mcp
  2. 在 profile 的 cordis.patch.yml 末尾追加一段 mcp-playwright 配置

改完需要重启 DSH，工具才会以 mcp__playwright__* 的形式出现。

用法
----
  python3 install_dsh.py --dry-run     # 只打印将要做什么
  python3 install_dsh.py               # 真正执行
  python3 install_dsh.py --uninstall   # 还原成安装前
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:  # 输出被重定向时编码可能不是 UTF-8，会让中文变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 插件版本必须和 DSH 本体对齐，否则升级后插件会加载失败
# （实测：DSH 0.2.1-rc.5 + 插件 0.2.1-rc.3 → 所有 mcp__playwright__* 工具消失）
DEFAULT_CLIENT_VERSION = "0.2.1-rc.5"
PLAYWRIGHT_MCP_PKG = "@playwright/mcp@0.0.83"
MARKER = "# >>> browser-handoff (mcp-playwright)"


# ---------------------------------------------------------------- 环境探测

def dsh_home() -> str:
    return os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")


def find_profile(name: str | None) -> tuple[str, str]:
    """返回 (profile 目录, profile 名)。"""
    root = os.path.join(dsh_home(), "profiles")
    if name:
        path = os.path.join(root, name)
        if os.path.isdir(path):
            return path, name
        raise SystemExit("找不到 profile：%s" % path)

    if os.path.isdir(os.path.join(root, "desktop")):
        return os.path.join(root, "desktop"), "desktop"

    candidates = [d for d in sorted(os.listdir(root))
                  if os.path.isdir(os.path.join(root, d))] if os.path.isdir(root) else []
    if len(candidates) == 1:
        return os.path.join(root, candidates[0]), candidates[0]
    raise SystemExit(
        "无法确定 DSH profile。现有候选：%s\n请用 --profile <名字> 指定。"
        % (", ".join(candidates) or "（无）"))


def find_dsh_cli() -> str | None:
    env = os.environ.get("DSH_CLI")
    if env and os.path.isfile(env):
        return env

    candidates = [
        "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh",
        os.path.expanduser(
            "~/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"),
        "/opt/DeepSeek Harness/resources/runtime/cli/bin/dsh",
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand

    which = shutil.which("dsh")
    return which


def dsh_version(cli: str) -> str | None:
    """问 dsh 自己的版本号，插件版本要跟它对齐。"""
    try:
        out = subprocess.run([cli, "--version"], capture_output=True,
                             text=True, timeout=20)
        blob = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?", blob)
        return m.group(0) if m else None
    except Exception:
        return None


def resolve_client_pkg(cli: str) -> tuple[str, str]:
    """返回 (客户端包全名, 版本号)。"""
    ver = dsh_version(cli) or DEFAULT_CLIENT_VERSION
    return "@deepseek-ai/dsh-mcp-client@" + ver, ver


def find_node() -> str | None:
    env = os.environ.get("NODE_BIN")
    if env and os.path.isfile(env):
        return env

    patterns = [
        os.path.join(dsh_home(), "dsh-runtimes", "*", "dependencies", "node", "bin", "node"),
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


def fwd(path: str) -> str:
    """YAML 里把反斜杠换成斜杠（防御性处理）。"""
    return path.replace("\\", "/")


# ---------------------------------------------------------------- 配置生成

def build_block(node: str, profile_dir: str, home: str) -> str:
    cli = os.path.join(profile_dir, "node_modules", "@playwright", "mcp", "cli.js")
    workdir = os.path.join(home, "browser-handoff-artifacts")
    cache = os.path.join(home, "Library", "Caches", "ms-playwright") \
        if sys.platform == "darwin" else os.path.join(home, ".cache", "ms-playwright")
    user_profile = os.path.join(home, ".dsh", "playwright-profile")
    return f"""{MARKER}
# 由 browser-handoff 安装器生成。删除本块并重启即可卸载。
- insert:
    - id: mcp-playwright
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: playwright
        transport: stdio
        command: {fwd(node)}
        args:
          - {fwd(cli)}
          - --browser
          - chrome
          - --user-data-dir
          - {fwd(user_profile)}
          - --no-sandbox
          - --output-dir
          - {fwd(workdir)}
        env:
          HOME: {fwd(home)}
          PLAYWRIGHT_BROWSERS_PATH: {fwd(cache)}
        toolCallTimeoutMs: 120000
"""


def patch_file(profile_dir: str) -> str:
    return os.path.join(profile_dir, "cordis.patch.yml")


def has_marker(path: str) -> bool:
    return os.path.isfile(path) and MARKER in open(path, encoding="utf-8").read()


def already_installed(path: str) -> bool:
    """配置里是否已经有这一项（兼容手工装过的 id）。"""
    if not os.path.isfile(path):
        return False
    text = open(path, encoding="utf-8").read()
    return MARKER in text or "id: mcp-playwright" in text


def strip_managed_blocks(path: str) -> bool:
    """删掉安装器写过的所有块（按最早的标记截断）。"""
    if not os.path.isfile(path):
        return False
    text = open(path, encoding="utf-8").read()
    idxs = [i for i in (text.find(MARKER), text.find(BRIDGE_MARKER),
                        text.find(EDGE_MARKER)) if i >= 0]
    if not idxs:
        return False
    open(path, "w", encoding="utf-8").write(text[:min(idxs)].rstrip() + "\n")
    return True


def installed_client_version(profile_dir: str) -> str | None:
    p = os.path.join(profile_dir, "node_modules", "@deepseek-ai",
                     "dsh-mcp-client", "package.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("version")
    except Exception:
        return None


BRIDGE_MARKER = "# >>> browser-handoff (browser-bridge)"


def find_python() -> str | None:
    """找一个能跑 browser_bridge.py 的 python3（和 DSH 同源的优先）。"""
    env = os.environ.get("PYTHON_BIN")
    if env and os.path.isfile(env):
        return env

    patterns = [
        os.path.join(dsh_home(), "dsh-runtimes", "*", "dependencies",
                     "python", "bin", "python3"),
    ]
    for pat in patterns:
        for cand in sorted(glob.glob(pat)):
            if os.path.isfile(cand):
                return cand

    if sys.executable and os.path.isfile(sys.executable):
        return sys.executable
    return shutil.which("python3")


def bridge_block(python: str, home: str) -> str:
    dst = os.path.join(home, ".dsh", "browser-bridge", "browser_bridge.py")
    return f"""{BRIDGE_MARKER}
# 多浏览器桥：读取你正在用的 Safari / Chrome / Edge（工具名 mcp__browser__*）。
# 由宿主进程拉起，因此不受 bash 沙盒的 Apple Events 限制。
- insert:
    - id: mcp-browser
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: browser
        transport: stdio
        command: {fwd(python)}
        args:
          - {fwd(dst)}
        env:
          HOME: {fwd(home)}
          PATH: /usr/bin:/bin:/usr/sbin:/sbin
        toolCallTimeoutMs: 30000
"""


def install_bridge(profile_dir: str, home: str) -> None:
    """把 browser_bridge.py 装到 ~/.dsh 并在 profile 里登记。"""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "servers", "browser_bridge.py")
    if not os.path.isfile(src):
        print("  ! 包里缺少 servers/browser_bridge.py，跳过浏览器桥")
        return

    dst_dir = os.path.join(home, ".dsh", "browser-bridge")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "browser_bridge.py")
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o755)
    print("  已安装桥脚本:", dst)

    pf_path = patch_file(profile_dir)
    text = open(pf_path, encoding="utf-8").read()
    if "id: mcp-browser" in text:
        print("  配置已存在 mcp-browser，跳过")
        return

    python = find_python()
    if not python:
        print("  ! 找不到 python3，无法登记浏览器桥（脚本已就位，可手动加配置）")
        return
    with open(pf_path, "a", encoding="utf-8") as f:
        f.write("\n" + bridge_block(python, home))
    print("  已登记 mcp-browser（python:", python, ")")


EDGE_MARKER = "# >>> browser-handoff (edge channel)"


def edge_block(node: str, profile_dir: str, home: str) -> str:
    """第二个 Playwright 实例，但底层用真实的 Microsoft Edge。"""
    cli = os.path.join(profile_dir, "node_modules", "@playwright", "mcp", "cli.js")
    workdir = os.path.join(home, "browser-handoff-artifacts")
    cache = (os.path.join(home, "Library", "Caches", "ms-playwright")
             if sys.platform == "darwin"
             else os.path.join(home, ".cache", "ms-playwright"))
    return f"""{EDGE_MARKER}
# 用 Playwright 启动真正的 Microsoft Edge（独立 profile），可完整操控。
# 工具名 mcp__edge__*，与 mcp__playwright__* 同一套，只是浏览器不同。
- insert:
    - id: mcp-edge
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: edge
        transport: stdio
        command: {fwd(node)}
        args:
          - {fwd(cli)}
          - --browser
          - msedge
          - --user-data-dir
          - {fwd(os.path.join(home, '.dsh', 'edge-profile'))}
          - --no-sandbox
          - --output-dir
          - {fwd(workdir)}
        env:
          HOME: {fwd(home)}
          PLAYWRIGHT_BROWSERS_PATH: {fwd(cache)}
        toolCallTimeoutMs: 120000
"""


def install_edge(profile_dir: str, home: str) -> None:
    pf_path = patch_file(profile_dir)
    text = open(pf_path, encoding="utf-8").read()
    if "id: mcp-edge" in text:
        print("  配置已存在 mcp-edge，跳过")
        return
    node = find_node()
    if not node:
        print("  ! 找不到 node，跳过 Edge 通道")
        return
    with open(pf_path, "a", encoding="utf-8") as f:
        f.write("\n" + edge_block(node, profile_dir, home))
    print("  已登记 mcp-edge（真实 Microsoft Edge，独立 profile）")


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="给 DSH 安装 Playwright MCP 浏览器工具")
    ap.add_argument("--profile", help="profile 名字，默认 desktop")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不做修改")
    ap.add_argument("--uninstall", action="store_true", help="还原到安装前")
    ap.add_argument("--with-bridge", action="store_true",
                    help="额外安装浏览器桥（读取你正在用的 Safari/Chrome/Edge，仅 macOS）")
    ap.add_argument("--with-edge", action="store_true",
                    help="额外开一条 Edge 通道（PowerShell/自动化用真实的 Microsoft Edge）")
    ap.add_argument("--all", action="store_true",
                    help="等于 --with-bridge --with-edge")
    ap.add_argument("--check", action="store_true",
                    help="只体检：版本是否对齐、配置是否在。退出码 0=正常 1=需要修复")
    args = ap.parse_args()

    if sys.platform != "darwin":
        raise SystemExit(
            "browser-handoff 仅支持 macOS。\n"
            "原因：浏览器桥依赖 AppleScript，Playwright 的 Edge 通道也按 macOS 的路径布局适配。\n"
            "当前系统：%s" % sys.platform)

    if args.all:
        args.with_bridge = True
        args.with_edge = True

    profile_dir, profile_name = find_profile(args.profile)
    pf = patch_file(profile_dir)
    backup = pf + ".bak-browser-handoff"
    node = find_node()
    cli = find_dsh_cli()
    home = os.path.expanduser("~")

    print("DSH_HOME :", dsh_home())
    print("profile  :", profile_name, "->", profile_dir)
    print("dsh CLI  :", cli or "（找不到）")
    print("node     :", node or "（找不到）")
    if has_marker(pf):
        state = "已安装（本安装器）"
    elif already_installed(pf):
        state = "已有 mcp-playwright（手工配置）"
    else:
        state = "未安装"
    print("patch    :", pf, state)

    if args.check:
        current = dsh_version(cli) if cli else None
        installed = installed_client_version(profile_dir)
        has_cfg = already_installed(pf)
        text = open(pf, encoding="utf-8").read() if os.path.isfile(pf) else ""
        ok = bool(current and installed and current == installed and has_cfg)
        print("\n体检结果：")
        print("  DSH 版本     :", current or "未知")
        print("  插件已装版本 :", installed or "未安装")
        print("  主通道 (playwright):", "存在" if has_cfg else "缺失")
        print("  桥 (browser)       :", "存在" if "id: mcp-browser" in text else "未装")
        print("  Edge 通道 (edge)   :", "存在" if "id: mcp-edge" in text else "未装")
        if ok:
            print("  ✅ 版本一致、主通道配置完整")
            return 0
        print("  ❌ 需要修复：python3 install_dsh.py  然后完全重启 DSH")
        return 1

    if args.uninstall:
        if os.path.isfile(backup):
            shutil.copyfile(backup, pf)
            print("\n已用备份还原：", backup)
        elif strip_managed_blocks(pf):
            print("\n已删除 patch 里所有 browser-handoff 管理的块"
                  "（playwright / browser / edge）。")
        else:
            print("\n没有找到可还原的内容。")
            return
        print("重启 DSH 后浏览器工具会消失。")
        return

    if not node:
        raise SystemExit("找不到 node。请先装 Node.js，或用 NODE_BIN=/path/to/node 指定。")
    if not cli:
        raise SystemExit(
            "找不到 dsh 命令行工具。请手动执行：\n"
            "  dsh plugin --profile %s add @deepseek-ai/dsh-mcp-client@%s %s\n"
            "然后重新运行本脚本（它会跳过安装、只写配置）。"
            % (profile_name, DEFAULT_CLIENT_VERSION, PLAYWRIGHT_MCP_PKG))

    client_pkg, client_ver = resolve_client_pkg(cli)
    print("client   :", client_pkg,
          "(与 DSH %s 对齐)" % client_ver if client_ver else "")

    if args.dry_run:
        print("\n[dry-run] 将执行：")
        print("  %s plugin --profile %s add %s %s"
              % (cli, profile_name, client_pkg, PLAYWRIGHT_MCP_PKG))
        print("[dry-run] 将追加到 %s：\n" % pf)
        print(build_block(node, profile_dir, home))
        if args.with_bridge:
            print("[dry-run] 以及浏览器桥配置：\n")
            print(bridge_block(find_python() or "/usr/bin/python3", home))
        if args.with_edge:
            print("[dry-run] 以及 Edge 通道配置：\n")
            print(edge_block(node, profile_dir, home))
        return

    if not already_installed(pf) and os.path.isfile(pf):
        shutil.copyfile(pf, backup)
        print("\n已备份原配置 ->", backup)

    print("\n[1/2] 安装插件 ...")
    cmd = [cli, "plugin", "--profile", profile_name, "add",
           client_pkg, PLAYWRIGHT_MCP_PKG]

    # DSH 正在运行时，profile 里的 package.json.lock 可能被占用，
    # 插件管理器会抛 EPERM/EBUSY。重试几次通常就过去了；仍然失败就给出明确指引。
    result = None
    for attempt in range(3):
        result = subprocess.run(cmd, text=True, capture_output=True)
        tail = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            break
        locked = ("EPERM" in tail or "package.json.lock" in tail
                  or "EBUSY" in tail or "resource busy" in tail.lower())
        if locked and attempt < 2:
            print("  profile 被占用（DSH 可能正在运行），2 秒后重试 %d/2 ..."
                  % (attempt + 1))
            time.sleep(2)
            continue
        break

    tail = (result.stdout or "") + (result.stderr or "")
    print(tail.strip()[-800:])
    if result.returncode != 0:
        if "EPERM" in tail or "package.json.lock" in tail:
            raise SystemExit(
                "插件安装失败：profile 被占用。\n"
                "→ 完全退出 DSH（托盘图标 → 退出）后重跑本脚本。")
        raise SystemExit("插件安装失败，退出码 %d" % result.returncode)

    print("[2/2] 写入配置 ...")
    if has_marker(pf):
        print("配置已存在（本安装器写入），跳过。如需更新请先 --uninstall。")
    elif already_installed(pf):
        print("检测到已有 mcp-playwright 配置（手工添加），不会重复写入。")
        print("如需由安装器接管，请先手动删除该块再重跑本脚本。")
    else:
        with open(pf, "a", encoding="utf-8") as f:
            f.write("\n" + build_block(node, profile_dir, home))
        print("已追加到", pf)

    if args.with_bridge:
        print("[3/4] 安装浏览器桥 ...")
        install_bridge(profile_dir, home)

    if args.with_edge:
        print("[4/4] 开 Edge 通道 ...")
        install_edge(profile_dir, home)

    print("""
完成。下一步：
  1. 完全退出 DSH（不是关窗口），重新打开
  2. 新开一个会话
  3. 先跑一次自检（不弹窗，10 秒内出结果）：
       python3 scripts/browser_probe.py --self-test
  4. 让 agent 列一下工具：
       mcp__playwright__*  → 自动化（Chrome）
       mcp__edge__*        → 自动化（真实 Microsoft Edge，若开了 --with-edge）
       mcp__browser__*     → 读取你正在用的 Safari/Chrome/Edge（若装了 --with-bridge）

卸载：python3 install_dsh.py --uninstall && 重启 DSH
""")


if __name__ == "__main__":
    sys.exit(main() or 0)
