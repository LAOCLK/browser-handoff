# 来源说明

本目录（`windows/`）是 **Windows 移植版**，基于本仓库 `macos/` 的 macOS 实现（0.5.0）扩展而来。

## 改了什么

- 用「浏览器扩展 + 本地回环桥」替代 macOS 的 AppleScript 桥，因为 Windows 上
  UI Automation 读不到 Chromium 的标签页与页面内容
- 增加了 `click` / `type` / `snapshot` 工具，因此具备**操作**用户浏览器标签页的能力
- 补齐了本地桥的鉴权（共享令牌 + Host/Origin 校验 + UUID 命令 id）
- 修复了 MCP stdio 在 Windows 控制台代码页下的中文乱码问题

## 版本

当前收录：**0.7.2**。各版本变更见本目录 `CHANGELOG.md`。

## 说明

- 移植工作由第三方完成，本仓库仅作分发
- 除本目录 `CHANGELOG.md` 或文件头另有说明外，视同沿用仓库根目录的 MIT 许可
- 如果你是该目录的作者并希望调整署名或许可，请提交 Issue / PR
