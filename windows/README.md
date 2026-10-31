# browser-handoff

让 AI 助手操作**一个真实可见的 Chrome 窗口**：它自己打开网页、点、读，遇到需要登录的页面就停下来把窗口交给你，你登录完它接着干。

这套东西解决的是同一个老问题：**很多资料在登录墙后面**。纯 HTTP 抓取拿不到，而让 AI 猜你的账号密码更不行——正确做法是让它在门口停下，人来开门。

---

## 这是什么

Windows 上的浏览器插件，给 DeepSeek Harness 用。装完助手能做两件事：

- **驱动它自己开的浏览器**：`mcp__playwright__*`（Chrome）、`mcp__edge__*`（真实 Edge），
  25 个工具，点击、输入、多标签、抓数据、登录交接
- **读、点、填你正在用的浏览器**：`mcp__browser__*`，走你自己的 profile，
  所以登录态、已打开的页面都直接可用（需要装一次浏览器扩展）

**这是 Windows 专用包**（Windows 10/11）。主路径只有一条：装进 DSH（见下）。
另外两种用法（其它 MCP 客户端、独立脚本）是可选项，共用同一个浏览器 profile。

---

## 通用前置条件

1. **Chrome 或 Edge**（用系统已装的，不下载 Chromium）
2. **Node.js 18+**（DSH 通常自带）
3. 第一次运行时会自动拉取 `@playwright/mcp`

> DSH 自带的 Node 运行时**只有 `node`，没有 `npm` / `npx`**，所以独立脚本的 `--npx`
> 需要系统另外装过 Node.js；用 DSH 的话完全不需要。

---

## 装进 DSH（推荐）

```bash
python install/install_dsh.py --dry-run   # 先看它打算干什么
python install/install_dsh.py --all       # 真正执行（三个通道都装）
```

它会做四件事：

1. 往你的 DSH profile 里装 `@deepseek-ai/dsh-mcp-client` + `@playwright/mcp`
2. 在 `%USERPROFILE%\.dsh\profiles\desktop\cordis.patch.yml` 末尾追加通道配置
3. 装浏览器桥（本地服务 + 浏览器扩展，见下节；不加 `--with-bridge` 则不装）
4. **把使用手册装成 DSH 技能**（`%USERPROFILE%\.dsh\skills\browser-handoff\SKILL.md`）

第 4 步容易被忽略，但它决定了助手的行为：手册里写着什么时候该停下来把页面交给你、
哪些做法是禁止的（绕付费墙、连撞十几个源）。**不装它，助手就只是个会用浏览器的普通 agent。**

> 如果你的 DSH 用了自定义 `DSH_HOME`，手册会装到 `<DSH_HOME>\skills\browser-handoff\`，
> 安装器自动跟随，不需要你改路径。

#### 装完先验收（几十秒）

```bash
python install/install_dsh.py --check
```

必须看到这四行都是「存在」，**并且退出码是 0**：

```
  主通道 (playwright): 存在
  桥 (browser)       : 存在（扩展目录已就位）
  Edge 通道 (edge)   : 存在
  使用手册 (skill)   : 存在 -> ...\.dsh\skills\browser-handoff\SKILL.md
  ✅ 版本一致、主通道配置完整、交回规则已就位
```

只要有一项缺失，脚本会以退出码 1 结束并提示重跑安装器 —— 不要在这种情况下继续用，
否则又会退回「遇到付费墙自己乱撞」的老问题。

**然后必须完全退出 DSH 再重开**（Windows 是托盘图标 → 退出；不是关窗口）。重启后新开一个会话，让助手列一下工具——应该看到一批 `mcp__playwright__*`（`@playwright/mcp` 0.0.83 是 25 个，版本不同数量会变），技能列表里也应该出现 `browser-handoff`。

> **版本会自动对齐**：安装器跑 `dsh --version` 读出你的 DSH 版本，然后安装**同版本**的
> `@deepseek-ai/dsh-mcp-client`。这一步很关键——版本错位时插件会静默加载失败，
> 表现就是「工具一个都不出现」。`@playwright/mcp` 固定在实测过的 0.0.83。

> **升级 DSH 之后**：工具会消失。重跑一次 `install_dsh.py` 再重启即可，
> 它会自动换成新版本号（`--uninstall` 不是必须的，脚本是幂等的）。

### 可选：多开几条浏览器通道

```bash
python install/install_dsh.py --with-edge      # 再加一条：真实 Microsoft Edge
python install/install_dsh.py --with-bridge    # 再加一条：读你正在用的 Edge / Chrome
python install/install_dsh.py --all            # 三个都要
```

装上 `--with-edge` 后，用户说「用 Edge 打开某站」时 agent 可以照做——它启动的是**真正的
Microsoft Edge**（独立 profile，登录态持久），不是"只能读的那个"。

### 浏览器桥：读你正在用的浏览器

桥由「本地服务 + 浏览器扩展」组成：

1. 安装器把桥服务和扩展复制到 `%USERPROFILE%\.dsh\browser-bridge\`
2. 在 `cordis.patch.yml` 里登记 `mcp-browser`（命令是 DSH 自带的 python）
3. **你需要手动装一次扩展**（只需一次）：
   - 打开 `edge://extensions` 或 `chrome://extensions`
   - 打开左下角「开发人员模式」
   - 点「加载解压缩的扩展」，选择 `%USERPROFILE%\.dsh\browser-bridge\extension`

装好后**不需要重启浏览器**，也不需要重启 DSH（扩展会自动连上）。Edge 和 Chrome
各装一次，两个浏览器就都能读。

| | Edge / Chrome（Windows） |
|---|---|
| 列标签、看当前页、开新标签 | ✅ |
| 读正文 | ✅ 无需任何浏览器设置（走 `chrome.scripting`） |
| **页面快照 + 点击 + 填表** | ✅ 在你**当前那个标签页**里操作，登录态原样可用 |
| 拖拽、滚轮、多标签协同 | ❌（要这些请用 `mcp__playwright__*` / `mcp__edge__*`） |
| 浏览器内置页面（`edge://`、扩展商店） | ❌ 不允许注入，会返回明确提示 |

### 在你正在看的页面上操作

`snapshot` 会给当前页每个可交互元素分配一个 `ref`，然后就能按 ref 点、按 ref 填：

```
snapshot  → - textbox "搜索词" [ref=e1]
            - button "提交" [ref=e2]
click     {ref: "e2"}
type      {ref: "e1", text: "机械臂", submit: true}
```

页面一变（跳转、局部刷新、弹层），旧的 ref 就失效，**重新 snapshot 再操作**。
这是 DOM 层操作，不是模拟真人输入：极少数风控严格的站点（支付、强人机校验）可能不接受，
遇到就换 Playwright 通道或让用户自己点。

权限说明：扩展需要「读取和更改你在所有网站上的数据」才能取当前页正文；
它只连 `127.0.0.1`，不向任何外部服务器发送数据。桥服务只监听回环地址。

不装扩展也完全没问题：`mcp__browser__*` 这四个工具会提示「没有检测到已连接的
浏览器扩展」，其余通道（Playwright / Edge / 文献脚本）不受影响。

排查（不启动浏览器，只验证桥的协议）：

```bash
python scripts/bridge_selftest.py
```

卸载：

```bash
python install/install_dsh.py --uninstall   # 然后重启 DSH
```

体检（升级后、或工具突然不见时先跑这个）：

```bash
python install/install_dsh.py --check   # 退出码 0=正常，1=需要修复
```

## 可选：其它 MCP 客户端（Claude Desktop / Cursor / VS Code）

把对应文件的内容合并进你的配置（**别整个覆盖**，只加 `mcpServers` 里那一项）：

| 客户端 | 文件 | 合并到 |
|---|---|---|
| Claude Desktop | `install/claude-desktop.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `install/cursor-mcp.json` | `%USERPROFILE%\.cursor\mcp.json` |
| VS Code | `install/vscode-mcp.json` | 项目里的 `.vscode/mcp.json` |

改完重启客户端。这些客户端里 MCP 是原生支持的，不需要任何插件系统。

> Windows 提示：如果客户端报找不到 `npx`，把 JSON 里的 `"command": "npx"` 改成 `"npx.cmd"`。

## 文献场景：先查免费全文，再决定要不要开浏览器

```bash
python scripts/lit_search.py --author "Jane Smith"
python scripts/lit_search.py --doi 10.1126/science.aaq0216
```

输出会标注 `OA`（有合法免费副本，直接取）或 `CLOSED`（没有免费副本，交给用户用自己的权限访问）。

下载也做完了：`python scripts/lit_fetch.py --doi <DOI>` 会自动找合法的 OA 副本、
校验确实是 PDF、按「年份-作者-标题」命名存好。闭源的一律拒绝，并说明原因。

格式：第一次会问「PDF 还是 HTML」，10 秒不回答按 PDF；选了会记住
（`%USERPROFILE%\.dsh/lit-fetch.json`），之后用 `--set-format` 改。

一批 DOI 可以先分流：`python scripts/lit_triage.py --dois-file dois.txt --download`
——免费的自动下载，付费的写进 `needs-login.txt` 等你点名。

导出文件也能直接解析：`python scripts/parse_export.py --latest` ——
自动找 `~/Downloads` 里最新的 RIS/BibTeX/TSV，去重排序出表。

**只调用开放 API**：OpenAlex / Crossref / Unpaywall / arXiv。
不抓 Web of Science、Scopus、知网、Google Scholar，不碰 Sci-Hub——这些不是技术限制，是法律和条款边界。

## 可选：独立脚本（不用 DSH）

```bash
# 打开页面并取可访问性快照
python scripts/browser_probe.py --url https://example.com --snapshot

# 自动滚动整页、抽正文存成文件（对付 canvas 渲染的在线文档）
python scripts/browser_probe.py --url "<地址>" --extract out.txt

# 撞到登录墙时停下来等你登录，登完自动继续
python scripts/browser_probe.py --url https://github.com/settings/profile \
    --wait-login 600 --snapshot
```

它会自己找 node 和 `@playwright/mcp`（依次找：环境变量 → DSH profile 的 node_modules → 全局 npm）。找不到就加 `--npx` 现场拉取（需要系统装了 npm）：

```bash
python scripts/browser_probe.py --url https://example.com --npx --snapshot
```

脚本还内置了**受限环境自动降级**：如果它发现 `$HOME` 不可写（容器、沙盒），会自动把缓存和 profile 挪到输出目录，并加上 `--no-sandbox`，不用你手动配。

---

## 怎么确认装好了

最短的一条命令（无头、不弹窗、大约 10 秒）：

```bash
python scripts/browser_probe.py --self-test
```

它依次验证：能起浏览器、能拿到工具列表、页面能渲染、页面里能跑 JS。
四项全绿才算真的通了。

想把所有能本地验证的都跑一遍：

```bash
python scripts/smoke_test.py
```

（编译检查 + 三份配置块生成 + 浏览器自检 + 本机安装状态，约 15 秒）

想单独验证 Edge 通道：

```bash
python scripts/smoke_test.py --browser msedge
```


在 DSH 里问助手「列一下你现在有哪些浏览器工具」，或者在别的客户端里直接说「打开 example.com，告诉我标题」。

正确的表现是：**屏幕上弹出一个 Chrome 窗口**，然后助手报出页面内容。

## 这就是日常用法

```
你：帮我查一下 <某站点> 上关于 <主题> 的资料，需要登录的话我来登

助手：→ 打开页面 → 检测到登录墙 → 停下来
      「窗口已经打开，请登录，登完我就继续」
你：  （在窗口里登录）
助手：→ 检测到登录成功 → 继续抓取 → 把内容整理给你
```

登录态会保存在 profile 里，同一个站点**下次不用再登**。

---

## 常见问题

**Q: 窗口没出现？**
检查是不是误加了 `--headless`。DSH 配置里默认没有，脚本里也默认有头。

**Q: Chrome 起不来，报 `sandbox initialization failed`？**
加 `--no-sandbox`（脚本参数 / 配置里的 args）。在容器、沙盒、部分公司策略下必须加。

**Q: 报 `EPERM: mkdir .../ms-playwright`？**
进程无权写缓存目录。把环境变量 `HOME` 指到可写位置，或设 `PLAYWRIGHT_BROWSERS_PATH`。

**Q: 某站点一直 403，登录了也没用？**
那是反自动化（浏览器指纹），不是登录问题。换信源，别在指纹上对抗。

**Q: 会不会看到我的密码？**
不会。助手只读页面上的元素状态（有没有密码框、有没有用户名 meta），你在窗口里自己输入。

**Q: 安全边界是什么？**
浏览器跑在 DSH 宿主进程下，**不受 DSH 的文件沙盒约束**，等于多了一个能读写你磁盘的执行者。介意的话别装进 DSH，改用独立脚本（它继承你终端的权限）。任何时候都能用 `--uninstall` 回滚。

Windows 的桥额外多一层边界：桥只监听 `127.0.0.1`，并且**有令牌鉴权**（安装器生成的随机串，
写在 `token.txt` 与扩展的 `token.js` 里）；`Host` / `Origin` 也做校验，网页脚本伪造不了结果。
浏览器扩展需要「读取和更改你在所有网站上的数据」才能取正文和点击，但它只与本机回环通信，
不向任何外部服务器发送数据。不装扩展时 `mcp__browser__*` 只会返回「没有检测到扩展」。

细节和踩坑记录见 `docs/gotchas.md`，给模型看的操作规范见 `SKILL.md`。
