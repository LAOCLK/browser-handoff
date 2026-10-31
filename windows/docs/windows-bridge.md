# Windows 浏览器桥：实现说明与排查

「读你正在用的浏览器」需要摸到浏览器内部。Windows 的 UI Automation 默认读不到
Chromium 的标签和网页内容（实测树是空的），所以这里用的是
**本地桥服务 + 浏览器扩展**。

## 架构

```
DSH 宿主进程
  └─ python browser_bridge_win.py      ← MCP stdio（工具名 mcp__browser__*）
       └─ HTTP 服务 127.0.0.1:8790-8799
             ▲ GET /poll（长轮询，最多挂 15 秒）      │ POST /result
             └── 扩展 service worker（Edge / Chrome）
                   └─ chrome.tabs / chrome.scripting
                         └─ 用户自己的 profile（登录态、已开标签页）
```

要点：

- 桥服务只监听**回环地址**，不对外暴露端口。
- 扩展不访问任何外部网络，只向 `127.0.0.1` 的长轮询接口发请求。
- 读正文走 `chrome.scripting.executeScript`，取的是当前标签页 `document.body.innerText`。
- 桥由 DSH 宿主进程拉起，因此不受 DSH 文件沙盒约束。

## 七个工具

| 工具 | 参数 | 返回 |
|---|---|---|
| `list_tabs` | `browser: edge\|chrome` | `{browser, count, tabs:[{window,tab,title,url}]}` |
| `current_tab` | 同上 | `{browser, title, url}` |
| `get_text` | 同上 | 正文纯文本，上限 20000 字 |
| `snapshot` | 同上 | 可交互元素列表，每项带 `ref`（如 `e3`） |
| `click` | 同上 + `ref` | `已点击 button "提交"（ref=e3）` |
| `type` | 同上 + `ref` / `text` / `submit` | `已向 ref=e4 输入 N 个字符` |
| `open_url` | 同上 + `url` | `已在 <浏览器> 打开：<url>` |

`browser` 不填时会自动选：两个浏览器都连着时优先 Edge，只连一个就用那个。

`snapshot` 会把 `ref` 写进元素的 `data-bh-ref` 属性，`click` / `type` 靠它定位。
页面跳转或重绘后旧 ref 失效，**重新 snapshot 再操作**。

## 端口

`8790-8799` 依次尝试，先占到先用。扩展会同时连接**所有**应答的端口，所以：

- 有残留的旧桥占着 8790，新桥用 8791 也能正常工作；
- DSH 重启后旧进程退出，扩展下一轮扫描（5 秒）自动接上新桥。

Windows 的 `SO_REUSEADDR` 允许两个进程同时绑同一端口，桥服务显式关掉了它，
否则会出现「请求随机落到另一个桥」的怪现象。

## 更新

插件升级后，桥服务和扩展文件都会被覆盖，但**已经加载进浏览器的扩展不会自动吃新代码**。
升级完到 `edge://extensions` 点一下扩展卡片上的「重新加载」（圆形箭头）即可。

## 安全边界

- 桥只监听 `127.0.0.1`，不对局域网或外部网络暴露。
- **有鉴权（0.7.0 起）**：安装器生成 128 位随机令牌，写在
  `<DSH_HOME>\browser-bridge\token.txt`，并写进扩展的 `token.js`。所有请求都要带令牌
  （GET 用 `?token=`，POST 用 `X-Bridge-Token`），否则 403。
- **失败关闭（0.7.2 起）**：令牌缺失或短于 16 字符时，桥**拒绝启动**（退出码 2），
  不会静默降级成无鉴权。只有显式设 `BROWSER_BRIDGE_SELFTEST=1` 或
  `BROWSER_BRIDGE_ALLOW_INSECURE=1` 才允许无令牌，且启动时打印警告。
- **Host / Origin 校验**：`Host` 不是 `127.0.0.1` / `localhost` 的拒绝（防 DNS rebinding）；
  带 `Origin` 且不是 `chrome-extension://` 的拒绝（挡网页 JS）。
- **命令 id 是 UUID**：网页既猜不到、也无法伪造 `/result`，避免了「把任意文本冒充成
  页面内容」的注入路径。
- 令牌文件只有本用户可读，所以**同用户的其它进程仍可能拿到令牌** —— 这是本机工具的信任边界，
  它挡的是「网页」和「别的账号」，不是「你自己的另一个程序」。
- 扩展需要「读取和更改你在所有网站上的数据」才能取正文和点击，这是 Chromium 对
  `chrome.scripting` 的硬性要求。它**不读** cookie、历史、下载记录，页面上也不留痕迹
  （ref 存在扩展的隔离世界，不写 DOM 属性）。
- 不想用了：`install_dsh.py --uninstall` + 浏览器里移除扩展 + 删掉
  `<DSH_HOME>\browser-bridge\`（含 token.txt），三个通道一起消失。

> 升级到 0.7.0 后：**重启 DSH + 重新加载扩展**要同时做。只重启不重载，
> 旧扩展没有令牌会被拒绝，工具会提示「请在 edge://extensions 点重新加载」。

## 排查

```bash
# 不启动浏览器，验证 MCP 契约 + 扩展协议（假扩展）
python scripts\bridge_selftest.py

# 看当前有哪些浏览器扩展连着
python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:8790/status')))"
```

| 现象 | 原因 / 处理 |
|---|---|
| 工具返回「没有检测到任何已连接的浏览器扩展」 | 扩展没装，或浏览器没开。按提示去 `edge://extensions` 加载 `%USERPROFILE%\.dsh\browser-bridge\extension` |
| 工具返回「<浏览器> 的扩展没有连接」 | 那个浏览器没开，或没给它装扩展（Edge 和 Chrome 要各装一次） |
| 浏览器重启后要点一下才生效 | 扩展 service worker 被休眠；正常使用时长轮询会保活，闲置很久后第一调用可能慢 1-2 秒 |
| `get_text` 返回「浏览器内置页面不允许读取」 | `edge://`、`chrome://`、扩展商店这类页面浏览器禁止注入脚本，属预期 |
| 加载扩展时报 manifest 错误 | 确认选中的是 `...\browser-bridge\extension` 这一层（里面有 `manifest.json`），不要选它的上级 |

## 卸载

```bash
python install\install_dsh.py --uninstall      # 去掉 profile 里的配置
```

然后到 `edge://extensions` 把 browser-handoff 扩展移除，删掉
`%USERPROFILE%\.dsh\browser-bridge\` 即可。重启 DSH 后 `mcp__browser__*` 消失。
