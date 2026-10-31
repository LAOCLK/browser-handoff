# browser-handoff

> **仅支持 macOS**。浏览器桥依赖 AppleScript，Edge 通道按 macOS 的应用布局适配。

让 AI 助手操作**一个真实可见的 Chrome 窗口**：它自己打开网页、点、读，遇到需要登录的页面就停下来把窗口交给你，你登录完它接着干。

这套东西解决的是同一个老问题：**很多资料在登录墙后面**。纯 HTTP 抓取拿不到，而让 AI 猜你的账号密码更不行——正确做法是让它在门口停下，人来开门。

---

## 先用三十秒判断你属于哪种情况

| 你的情况 | 走哪条路 | 需要什么 |
|---|---|---|
| 装了 **DeepSeek Harness (DSH)** | `install/install_dsh.py` | DSH + Node（DSH 通常自带） |
| 用 **Claude Desktop / Cursor / VS Code** | 复制 `install/` 里对应的 JSON | 这些客户端 + Node |
| 什么都没有，只想跑个脚本 | `scripts/browser_probe.py` | Python 3 + Node + Chrome |

三条路可以共存，共用同一个浏览器 profile（登录一次，到处都认）。

---

## 通用前置条件

1. **Chrome 或 Edge**（默认用系统 Chrome，不会下载 Chromium，省几百 MB）
2. **Node.js 18+**（`node -v` 能跑就行）
3. 第一次运行时会自动拉取 `@playwright/mcp`

> 注意：DSH 自带的 Node 运行时**只有 `node`，没有 `npm` / `npx`**。
> 如果你走路径 C 且系统里没装过 Node，`--npx` 用不了——先跑一次路径 A 的安装器把包装上即可。

---

## 路径 A：DSH

```bash
python3 install/install_dsh.py --dry-run   # 先看它打算干什么
python3 install/install_dsh.py             # 真正执行
```

它会做两件事：

1. 往你的 DSH profile 里装 `@deepseek-ai/dsh-mcp-client` + `@playwright/mcp`
2. 在 `~/.dsh/profiles/desktop/cordis.patch.yml` 末尾追加一段 `mcp-playwright` 配置

**然后必须完全退出 DSH 再重开**（⌘Q，不是关窗口）。重启后新开一个会话，让助手列一下工具——应该看到一批 `mcp__playwright__*`（`@playwright/mcp` 0.0.83 是 25 个，版本不同数量会变）。

> **版本会自动对齐**：安装器跑 `dsh --version` 读出你的 DSH 版本，然后安装**同版本**的
> `@deepseek-ai/dsh-mcp-client`。这一步很关键——版本错位时插件会静默加载失败，
> 表现就是「工具一个都不出现」。`@playwright/mcp` 固定在实测过的 0.0.83。

> **升级 DSH 之后**：工具会消失。重跑一次 `install_dsh.py` 再重启即可，
> 它会自动换成新版本号（`--uninstall` 不是必须的，脚本是幂等的）。

### 可选：多开几条浏览器通道

```bash
python3 install/install_dsh.py --with-edge      # 再加一条：真实 Microsoft Edge
python3 install/install_dsh.py --with-bridge    # 再加一条：读你正在用的 Safari/Chrome/Edge
python3 install/install_dsh.py --all            # 两个都要
```

装上 `--with-edge` 后，用户说「用 Edge 打开某站」时 agent 可以照做——它启动的是**真正的
Microsoft Edge**（独立 profile，登录态持久），不是"只能读的那个"。

### 浏览器桥：读你正在用的浏览器（macOS）

会额外装一个小的 MCP 服务器，让助手能读到**你此刻开着的** Safari / Chrome / Edge：
标签页列表、当前页、正文，还能在那边开新标签。

| | Safari | Chrome / Edge |
|---|---|---|
| 列标签、看当前页、开新标签 | ✅ | ✅ |
| 读正文 | ✅ 无需任何设置 | ⚠️ 要开「查看 → 开发者 → 允许 Apple 事件中的 JavaScript」 |

为什么需要单独一个服务：Playwright 只驱动 Chromium，看不到 Safari；而在 DSH 的
bash 沙盒里跑 AppleScript 会被拦（`-10004 权限违例`）。这个桥由宿主进程拉起，
不受那层沙盒限制。细节见 `docs/gotchas.md` 第 7 条。

卸载：

```bash
python3 install/install_dsh.py --uninstall   # 然后重启 DSH
```

体检（升级后、或工具突然不见时先跑这个）：

```bash
python3 install/install_dsh.py --check   # 退出码 0=正常，1=需要修复
```

## 路径 B：Claude Desktop / Cursor / VS Code

把对应文件的内容合并进你的配置（**别整个覆盖**，只加 `mcpServers` 里那一项）：

| 客户端 | 文件 | 合并到 |
|---|---|---|
| Claude Desktop | `install/claude-desktop.json` | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor | `install/cursor-mcp.json` | `~/.cursor/mcp.json` |
| VS Code | `install/vscode-mcp.json` | 项目里的 `.vscode/mcp.json` |

改完重启客户端。这些客户端里 MCP 是原生支持的，不需要任何插件系统。

## 文献场景：先查免费全文，再决定要不要开浏览器

```bash
python3 scripts/lit_search.py --author "Jane Smith"
python3 scripts/lit_search.py --doi 10.1126/science.aaq0216
```

输出会标注 `OA`（有合法免费副本，直接取）或 `CLOSED`（没有免费副本，交给用户用自己的权限访问）。

下载也做完了：`python3 scripts/lit_fetch.py --doi <DOI>` 会自动找合法的 OA 副本、
校验确实是 PDF、按「年份-作者-标题」命名存好。闭源的一律拒绝，并说明原因。

格式：第一次会问「PDF 还是 HTML」，10 秒不回答按 PDF；选了会记住
（`~/.dsh/lit-fetch.json`），之后用 `--set-format` 改。

一批 DOI 可以先分流：`python3 scripts/lit_triage.py --dois-file dois.txt --download`
——免费的自动下载，付费的写进 `needs-login.txt` 等你点名。

导出文件也能直接解析：`python3 scripts/parse_export.py --latest` ——
自动找 `~/Downloads` 里最新的 RIS/BibTeX/TSV，去重排序出表。

**只调用开放 API**：OpenAlex / Crossref / Unpaywall / arXiv。
不抓 Web of Science、Scopus、知网、Google Scholar，不碰 Sci-Hub——这些不是技术限制，是法律和条款边界。

## 路径 C：只想跑脚本

```bash
# 打开页面并取可访问性快照
python3 scripts/browser_probe.py --url https://example.com --snapshot

# 自动滚动整页、抽正文存成文件（对付 canvas 渲染的在线文档）
python3 scripts/browser_probe.py --url "<地址>" --extract out.txt

# 撞到登录墙时停下来等你登录，登完自动继续
python3 scripts/browser_probe.py --url https://github.com/settings/profile \
    --wait-login 600 --snapshot
```

它会自己找 node 和 `@playwright/mcp`（依次找：环境变量 → DSH profile 的 node_modules → 全局 npm）。找不到就加 `--npx` 现场拉取（需要系统装了 npm）：

```bash
python3 scripts/browser_probe.py --url https://example.com --npx --snapshot
```

脚本还内置了**受限环境自动降级**：如果它发现 `$HOME` 不可写（容器、沙盒），会自动把缓存和 profile 挪到输出目录，并加上 `--no-sandbox`，不用你手动配。

---

## 怎么确认装好了

最短的一条命令（无头、不弹窗、大约 10 秒）：

```bash
python3 scripts/browser_probe.py --self-test
```

它依次验证：能起浏览器、能拿到工具列表、页面能渲染、页面里能跑 JS。
四项全绿才算真的通了。

想把所有能本地验证的都跑一遍：

```bash
python3 scripts/smoke_test.py
```

（编译检查 + 三份配置块生成 + 浏览器自检 + 本机安装状态，约 15 秒）

想单独验证 Edge 通道：

```bash
python3 scripts/smoke_test.py --browser msedge
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
浏览器跑在 DSH 宿主进程下，**不受 DSH 的文件沙盒约束**，等于多了一个能读写你磁盘的执行者。介意的话别装路径 A，用路径 C 的脚本（它继承你终端的权限）。任何时候都能用 `--uninstall` 或删掉 JSON 条目回滚。

细节和踩坑记录见 `docs/gotchas.md`，给模型看的操作规范见 `SKILL.md`。
