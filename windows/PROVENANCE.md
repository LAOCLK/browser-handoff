# Windows 版说明

本目录是同一项目的 **Windows 实现**，与 `macos/` 出自同一作者、同一套设计。

## 为什么要单独一版

macOS 版靠 AppleScript 读浏览器，而 Windows 上没有等价能力——实测 UI Automation
读不到 Chromium 的标签页与网页内容（树是空的）。因此 Windows 版换了一条技术路线：

| | macOS | Windows |
|---|---|---|
| 接管浏览器 | AppleScript（进程外脚本） | 浏览器扩展 + 本地回环桥 |
| 通信 | 直接调用系统脚本接口 | `127.0.0.1:8790-8799` 长轮询 |
| 能力 | 只读（标签 / 正文 / 开新标签） | 读 + **点击 / 输入 / 快照** |
| 鉴权 | 系统级（无需令牌） | 共享令牌 + Host/Origin 校验 |

## 两个版本共享的部分

- 同一套 `SKILL.md` 行为规范（登录交回、人物核查、合规红线）
- 同一套文献工具（`lit_search` / `lit_fetch` / `lit_triage` / `parse_export`）
- 同一套安装器交互与 `--check` 体检设计

## 版本

当前收录：**0.7.2**。各版本变更见本目录 `CHANGELOG.md`。
许可沿用餐仓库根目录的 MIT。
