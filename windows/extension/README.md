# browser-handoff bridge 扩展

这个目录是浏览器扩展本体，由 `install/install_dsh.py` 复制到
`%USERPROFILE%\.dsh\browser-bridge\extension`。

它是 **Manifest V3** 扩展，只有后台 service worker，没有弹窗、没有页面注入脚本。

## 它做什么

通过 `127.0.0.1:8790-8799` 的长轮询，响应七个动作：

| 动作 | 用的浏览器接口 |
|---|---|
| 列标签页 | `chrome.tabs.query({})` |
| 当前标签页 | `chrome.tabs.query({active:true,lastFocusedWindow:true})` |
| 读正文 | `chrome.scripting.executeScript` → `document.body.innerText` |
| 页面快照 | 扫描可交互元素，打上 `data-bh-ref` 供后续操作 |
| 点击 | 按 ref 找元素 → `scrollIntoView` + `element.click()` |
| 填表 | 按 ref 找元素 → 原生 value setter + `input`/`change` 事件 |
| 开新标签 | `chrome.tabs.create` + `chrome.windows.update` |

它只在你（或助手）调用 `mcp__browser__*` 时才动作，不会定时抓取或点击任何东西。

## 令牌（0.7.0 起）

安装器会在本目录写一个 `token.js`，内容是 `self.BRIDGE_TOKEN = "<随机串>"`。
扩展的每个请求都要带这个令牌，桥服务端不匹配就 403 —— 这样本机其它程序或网页
既取不走命令，也伪造不了结果。

包里的 `token.js` 是空值（开发用）。**从本目录直接加载前请先跑一次安装器**，
否则令牌是空的，只能连上同样没配令牌的桥。

## 权限说明

- `tabs`：拿到标签页的标题和 URL
- `scripting` + `<all_urls>`：在被要求时读取/操作当前页（浏览器限制，必须给站点权限）。
  **这意味着它能读写所有网站的页面**（含网银、邮箱）；它只在你/助手明确调用工具时才动
- `alarms`：MV3 的 service worker 会被浏览器休眠，用它做兜底唤醒
- `http://127.0.0.1/*`：与本机桥通信

不访问任何外部网络，不做数据上传。

## 手动加载

1. 打开 `edge://extensions` 或 `chrome://extensions`
2. 打开「开发人员模式」
3. 「加载解压缩的扩展」→ 选中**本目录**（含 `manifest.json` 的这一层）

Edge 和 Chrome 要分别加载一次。装好后不用重启浏览器。
