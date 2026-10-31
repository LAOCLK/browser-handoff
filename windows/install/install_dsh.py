#!/usr/bin/env python
"""install_dsh.py — 给 DeepSeek Harness 安装浏览器工具（Windows 版）。

做的事（都可回滚）：
  1. 往当前 profile 里安装 @deepseek-ai/dsh-mcp-client 和 @playwright/mcp
  2. 在 profile 的 cordis.patch.yml 末尾追加通道配置：
       mcp-playwright  自动化（Chrome）
       mcp-edge        自动化（真实 Microsoft Edge，--with-edge）
       mcp-browser     读取你正在用的 Edge/Chrome（--with-bridge，需加载扩展）
  3. 把浏览器桥服务和扩展复制到 %USERPROFILE%\\.dsh\\browser-bridge\\

改完需要完全退出 DSH 再打开，工具才会出现。

用法
----
  python install_dsh.py --dry-run     # 只打印将要做什么
  python install_dsh.py --all         # 三个通道都装
  python install_dsh.py --check       # 体检
  python install_dsh.py --uninstall   # 还原成安装前
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

try:  # Windows 上输出被重定向时默认用 GBK，会让中文变乱码
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

    candidates = []
    for pat in (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                     "DeepSeek Harness", "resources", "runtime", "cli", "bin", "dsh.cmd"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                     "DeepSeek Harness", "resources", "runtime", "cli", "bin", "dsh"),
    ):
        candidates.append(pat)

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
        os.path.join(dsh_home(), "dsh-runtimes", "*", "dependencies", "node", "bin", "node.exe"),
        os.path.join(dsh_home(), "dsh-runtimes", "*", "dependencies", "node", "node.exe"),
    ]
    for pat in patterns:
        for cand in sorted(glob.glob(pat)):
            if os.path.isfile(cand):
                return cand

    which = shutil.which("node")
    if which:
        return which
    for cand in (r"C:\Program Files\nodejs\node.exe",):
        if os.path.isfile(cand):
            return cand
    return None


def fwd(path: str) -> str:
    """YAML 里把反斜杠换成斜杠，Windows 也能用。"""
    return path.replace("\\", "/")


# ---------------------------------------------------------------- 配置生成

def build_block(node: str, profile_dir: str, home: str) -> str:
    cli = os.path.join(profile_dir, "node_modules", "@playwright", "mcp", "cli.js")
    workdir = os.path.join(home, "browser-handoff-artifacts")
    cache = os.path.join(home, ".cache", "ms-playwright")
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
    """找一个能跑 browser_bridge_win.py 的 python（和 DSH 同源的优先）。"""
    env = os.environ.get("PYTHON_BIN")
    if env and os.path.isfile(env):
        return env

    patterns = [
        os.path.join(dsh_home(), "dsh-runtimes", "*", "dependencies",
                     "python", "python.exe"),
    ]
    for pat in patterns:
        for cand in sorted(glob.glob(pat)):
            if os.path.isfile(cand):
                return cand

    if sys.executable and os.path.isfile(sys.executable):
        return sys.executable
    return shutil.which("python")


def win_bridge_block(python: str, home: str) -> str:
    """Windows：python 服务 + 浏览器扩展，接口与上一个通道完全一致。"""
    dst = os.path.join(dsh_home(), "browser-bridge", "browser_bridge_win.py")
    return f"""{BRIDGE_MARKER}
# 多浏览器桥（Windows）：读取你正在用的 Edge / Chrome（工具名 mcp__browser__*）。
# 由宿主进程拉起；浏览器侧需要加载 browser-handoff 扩展。
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
        toolCallTimeoutMs: 60000
"""


def skill_path() -> str:
    """DSH 的用户技能目录：<dshHome>/skills/<name>/SKILL.md（跟随 DSH_HOME）。"""
    return os.path.join(dsh_home(), "skills", "browser-handoff", "SKILL.md")


REQUIRED_SKILL_MARKS = ("name: browser-handoff", "description:", "撞墙就交给用户")


def install_skill() -> None:
    """把使用手册装成 DSH 技能，模型才会看到「撞墙交回用户」这些规则。

    装完立刻回读校验：frontmatter 不完整或规则缺失就报错退出，
    不允许「看起来装好了、其实模型读不到」这种半成功状态。
    """
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md")
    if not os.path.isfile(src):
        raise SystemExit("包里缺少 SKILL.md —— 使用手册装不上，插件等于没有交回规则。")
    dst = skill_path()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)

    text = open(dst, encoding="utf-8").read()
    missing = [m for m in REQUIRED_SKILL_MARKS if m not in text]
    if not text.startswith("---") or missing:
        raise SystemExit(
            "使用手册复制后校验失败（缺少 %s）：%s\n"
            "→ 模型会因为读不到交回规则而自行绕路，请先修好再装。"
            % ("、".join(missing) or "frontmatter", dst))

    print("  已安装使用手册:", dst)
    print("     （DSH 会扫描 %s；新会话里能看到 browser-handoff 技能）"
          % os.path.dirname(dst))


def ensure_token(dst_dir: str) -> str:
    """生成（或复用）本地桥的共享令牌，写进 token.txt。

    令牌让「本机任意进程 / 任意网页」无法取走命令或伪造 /result。
    重装时复用旧令牌，避免正在运行的扩展突然失效。
    """
    path = os.path.join(dst_dir, "token.txt")
    if os.path.isfile(path):
        old = open(path, encoding="utf-8").read().strip()
        if len(old) >= 16:
            return old
    token = secrets.token_hex(16)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


def write_ext_token(dst_ext: str, token: str) -> str:
    """把令牌写进扩展的 token.js（service worker 用 importScripts 读取）。"""
    path = os.path.join(dst_ext, "token.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("// 由安装器写入；换机器/重装会重新生成。\n")
        f.write('self.BRIDGE_TOKEN = "%s";\n' % token)
    return path


def install_win_bridge(profile_dir: str, home: str) -> None:
    """Windows：装桥服务 + 扩展，并打印扩展加载步骤。"""
    pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    src_server = os.path.join(pkg, "servers", "browser_bridge_win.py")
    src_ext = os.path.join(pkg, "extension")
    if not os.path.isfile(src_server) or not os.path.isdir(src_ext):
        print("  ! 包里缺少 servers/browser_bridge_win.py 或 extension/，跳过浏览器桥")
        return

    dst_dir = os.path.join(dsh_home(), "browser-bridge")
    os.makedirs(dst_dir, exist_ok=True)
    dst_server = os.path.join(dst_dir, "browser_bridge_win.py")
    shutil.copyfile(src_server, dst_server)
    print("  已安装桥服务:", dst_server)

    dst_ext = os.path.join(dst_dir, "extension")
    shutil.copytree(src_ext, dst_ext, dirs_exist_ok=True)
    token = ensure_token(dst_dir)
    write_ext_token(dst_ext, token)
    print("  已安装浏览器扩展:", dst_ext)
    print("  本地桥令牌已就绪: %s\\token.txt（本机鉴权用，勿分享）" % dst_dir)

    pf_path = patch_file(profile_dir)
    text = open(pf_path, encoding="utf-8").read()
    if "id: mcp-browser" in text:
        print("  配置已存在 mcp-browser，跳过")
    else:
        python = find_python()
        if not python:
            print("  ! 找不到 python，无法登记浏览器桥（文件已就位，可手动加配置）")
            return
        with open(pf_path, "a", encoding="utf-8") as f:
            f.write("\n" + win_bridge_block(python, home))
        print("  已登记 mcp-browser（python:", python, "）")

    print("")
    print("  ⚠ 还需要在浏览器里加载一次扩展（只需一次，之后自动跟随）：")
    print("     1. 打开 edge://extensions 或 chrome://extensions")
    print("     2. 打开左下角「开发人员模式」")
    print("     3. 点「加载解压缩的扩展」，选择这个目录：")
    print("        " + dst_ext)
    print("     Edge 和 Chrome 各装一次的话，两个浏览器都能被读取。")
    print("")
    print("  权限说明：该扩展可读取并操作**所有网站**的页面内容（这是 chrome.scripting")
    print("  读正文/点击的必要条件）。它只连 127.0.0.1、不向外部网络发送任何数据。")


EDGE_MARKER = "# >>> browser-handoff (edge channel)"


def edge_block(node: str, profile_dir: str, home: str) -> str:
    """第二个 Playwright 实例，但底层用真实的 Microsoft Edge。"""
    cli = os.path.join(profile_dir, "node_modules", "@playwright", "mcp", "cli.js")
    workdir = os.path.join(home, "browser-handoff-artifacts")
    cache = os.path.join(home, ".cache", "ms-playwright")
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
    if sys.platform != "win32":
        raise SystemExit("这个包是 Windows 专用版，只支持 Windows 10/11。")

    ap = argparse.ArgumentParser(description="给 DSH 安装浏览器工具（Windows 版）")
    ap.add_argument("--profile", help="profile 名字，默认 desktop")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不做修改")
    ap.add_argument("--uninstall", action="store_true", help="还原到安装前")
    ap.add_argument("--with-bridge", action="store_true",
                    help="额外安装浏览器桥（读取你正在用的 Edge / Chrome）")
    ap.add_argument("--with-edge", action="store_true",
                    help="额外开一条 Edge 通道（PowerShell/自动化用真实的 Microsoft Edge）")
    ap.add_argument("--all", action="store_true",
                    help="等于 --with-bridge --with-edge")
    ap.add_argument("--check", action="store_true",
                    help="只体检：版本是否对齐、配置是否在。退出码 0=正常 1=需要修复")
    args = ap.parse_args()

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
        bridge_cfg = "id: mcp-browser" in text
        bridge_dir = os.path.join(dsh_home(), "browser-bridge")
        ext_dir = os.path.join(bridge_dir, "extension")
        ext_ok = os.path.isfile(os.path.join(ext_dir, "manifest.json"))

        # 本地桥鉴权：token.txt 与扩展里的 token.js 必须一致且非空
        token_file = os.path.join(bridge_dir, "token.txt")
        tok = ""
        if os.path.isfile(token_file):
            tok = open(token_file, encoding="utf-8").read().strip()
        ext_tok = ""
        ext_tok_file = os.path.join(ext_dir, "token.js")
        if os.path.isfile(ext_tok_file):
            m = re.search(r'BRIDGE_TOKEN\s*=\s*"([^"]*)"',
                          open(ext_tok_file, encoding="utf-8").read())
            ext_tok = (m.group(1) if m else "").strip()
        auth_ok = bool(tok) and tok == ext_tok

        skill_file = skill_path()
        skill_ok = False
        if os.path.isfile(skill_file):
            body = open(skill_file, encoding="utf-8").read()
            skill_ok = body.startswith("---") and all(
                m in body for m in REQUIRED_SKILL_MARKS)

        ok = bool(current and installed and current == installed and has_cfg
                  and skill_ok and (auth_ok or not bridge_cfg))
        print("\n体检结果：")
        print("  DSH 版本     :", current or "未知")
        print("  插件已装版本 :", installed or "未安装")
        print("  主通道 (playwright):", "存在" if has_cfg else "缺失")
        print("  桥 (browser)       :",
              ("存在（扩展目录已就位）" if bridge_cfg and ext_ok
               else ("配置在但扩展文件缺失" if bridge_cfg else "未装")))
        print("  桥鉴权 (token)     :",
              ("已启用" if auth_ok
               else ("未启用（重跑安装器生成令牌）" if bridge_cfg else "未装")))
        print("  Edge 通道 (edge)   :", "存在" if "id: mcp-edge" in text else "未装")
        print("  使用手册 (skill)   :",
              ("存在 -> %s" % skill_file) if skill_ok
              else ("文件在但内容不完整 -> %s" % skill_file if os.path.isfile(skill_file)
                    else "缺失（模型不知道交回规则，重跑安装器）"))
        if ok:
            print("  ✅ 版本一致、配置完整、交回规则就位、桥已鉴权")
            return 0
        print("  ❌ 需要修复：python install_dsh.py  然后完全重启 DSH")
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
        # 使用手册只在确认装了本插件的技能时移除，不碰别人的技能目录
        if os.path.isfile(skill_path()):
            shutil.rmtree(os.path.dirname(skill_path()), ignore_errors=True)
            print("已移除使用手册：", skill_path())
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
            print(win_bridge_block(find_python() or sys.executable, home))
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

    # Windows 上如果 DSH 正在运行，profile 里的 package.json.lock 会被占用，
    # 插件管理器会抛 EPERM。重试几次通常就过去了；仍然失败就给出明确指引。
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

    print("[手册] 安装使用手册 ...")
    install_skill()

    if args.with_bridge:
        print("[3/4] 安装浏览器桥 ...")
        install_win_bridge(profile_dir, home)

    if args.with_edge:
        print("[4/4] 开 Edge 通道 ...")
        install_edge(profile_dir, home)

    print("""
完成。下一步：
  1. 完全退出 DSH（不是关窗口），重新打开
  2. 新开一个会话
  3. 先跑一次自检（不弹窗，10 秒内出结果）：
       python scripts\\browser_probe.py --self-test
  4. 让 agent 列一下工具：
       mcp__playwright__*  → 自动化（Chrome）
       mcp__edge__*        → 自动化（真实 Microsoft Edge，若开了 --with-edge）
       mcp__browser__*     → 读取你正在用的 Edge/Chrome（若装了 --with-bridge）

如果不装扩展，mcp__browser__* 这四个工具会提示「没有检测到已连接的浏览器扩展」，
其余通道不受影响。

卸载：python install_dsh.py --uninstall && 重启 DSH
""")


if __name__ == "__main__":
    sys.exit(main() or 0)
