# 变更记录

## 0.7.5 — 修 0.7.4 的两处不一致

审查发现两处文档与实现不符，一并修掉：

- **`lit_triage.py --download` 的间隔从写死的 0.4 秒改成可配**：新增 `--delay`
  （默认 5 秒，实际取 `max(delay, 0.4)`）。0.4 秒正是会触发 `CPE00002` 的节奏，
  文档里却写着「每篇 5–10 秒」—— 现在代码和文档一致了
  （分流阶段仍保持 0.4 秒，那一步只查 OpenAlex/Unpaywall，不碰出版社）
- **失效引用**：`SKILL.md` 里「gotchas 第 12 条」改为「第 10 条」
  （Windows 版 gotchas 只有 11 条，虚拟滚动那条是第 10 条）
- `docs/gotchas.md` 第 11 条同步写明 `--delay` 默认 5 秒

## 0.7.4 — 补一条限流踩坑记录

文档补充，不改代码。真实日志里的一次事故：批量从 ScienceDirect 逐篇下载 PDF，
下了十几篇后全部失败，报 `CPE00002`，约 8 分钟才恢复。

`docs/gotchas.md` 新增第 11 条，要点：

- **现象**：开头正常，之后全失败，报 `CPE00002` 并给出 IP；等约 8 分钟恢复
- **原因**：Elsevier 按 IP 限频，机器节奏（每秒一篇）与人类节奏差异过大；
  被限后**同一机构 IP 下所有人**一起受影响
- **解法**：每篇间隔 5–10 秒、不并发不批量重试；被限后立即停手等 8–10 分钟；
  先下 OA 那批；触发后如实告诉用户「等约 8 分钟继续」
- **批量走 `lit_triage.py --download`**（串行 + 间隔），不要自己写 for 循环
- **别做**：换 UA、换代理池、并发重试 —— 属于绕过限流，且会让机构 IP 被封更久

## 0.7.3 — 遇到登录墙：先弹窗，再报告

实机日志发现的失败模式：检索出 50 篇订阅墙文献 + 3 篇被 Cloudflare 挡住后，
agent **只给了一张清单**，还反问「需要我把那几篇在你浏览器里逐篇打开、由你点下载吗？」。
用户得回一句「你应该打开浏览器让我进行机构登录」，它才真的弹窗（之后顺利拿到 32 篇 Elsevier）。

根因：它把「打开可见的浏览器窗口」当成需要征求同意的动作，于是用报告代替了交接。
插件存在的意义就是让它敢开窗，所以这条必须写死：

- **开窗口是预授权的**：不要问「要不要我打开」，直接开、开完再说
- **报告前必须先开页面**：任何「需要登录 / 订阅 / 付费 / 过验证」的回复之前，
  必须已经真实调用过 `open_url`（或 `browser_navigate`）；只给清单 = 流程失败
- **批量场景同样适用**：第一次判定到墙就打开第一本被挡的页面（或机构登录入口），
  清单可以同时列，顺序永远是「先弹窗，再报告」
- 附了一段标准话术模板，照着说即可

（技能正文每次加载都会重读文件，升级后新开会话即生效，不需要重启 DSH。）

## 0.7.2 — 令牌缺失不再静默降级（审查 R1）

之前 `token.txt` 丢了，桥会**悄悄以无鉴权模式继续跑**：删掉一个文件就能把 0.7.0 的鉴权
降级掉，而且工具照常能用，没人会发现。改法是**失败关闭**：

- 没有令牌（文件缺失，或内容短于 16 字符）时，桥**拒绝启动**，退出码 2，并在 stderr 说明
  「请重跑 install/install_dsh.py 生成令牌」
- 只有显式声明才允许无令牌：`BROWSER_BRIDGE_SELFTEST=1`（自检）或
  `BROWSER_BRIDGE_ALLOW_INSECURE=1`（开发），且启动时打印警告
- 自检与回归脚本相应改用显式逃生口；安全测试新增 2 项断言：
  「无令牌且未显式放行 -> 拒绝启动」「显式放行后 Origin 仍校验」
- 安装器的 `--check` 早已把令牌算进结论（缺失即退出码 1），现在运行时也不会再放过

## 0.7.1 — 修中文在 MCP stdio 上被解坏

0.7.0 装完实测发现：`type` 填「安全加固验收」，页面里变成 `瀹夊叏鍔犲浐楠屾敹`。

- 根因：桥服务把 stdout/stderr 切成了 UTF-8，**漏了 stdin**。DSH 启动进程时不设
  `PYTHONUTF8`，Python 用控制台代码页（cp936）读 stdin，请求里的中文参数一进来就乱码
- 修法：三个流统一 `reconfigure(encoding="utf-8")`
- 新增 `scripts/stdin_encoding_test.py`：用「去掉所有 UTF-8 环境变量」的默认环境复现，
  断言中文往返一致；已接进 `smoke_test.py`（新增「中文往返」一项）
- 该测试在修复前确认会失败（`瀹夊叏鍔犲浐楠屾敹`），修复后通过

## 0.7.0 — 安全加固（外部审查 P1/P3/P2/P4/P5）

外部审查指出：本地桥只绑回环，但**回环不是安全边界**。这一版补齐。

### P1 桥鉴权（重要）

- **共享令牌**：安装器生成 128 位随机串，写入 `<DSH_HOME>\browser-bridge\token.txt`，
  并写进扩展的 `token.js`；扩展所有请求都要带（GET 走 `?token=`，POST 走 `X-Bridge-Token`），
  服务端不匹配直接 403。没有令牌的旧扩展连不上 —— 升级后要点一次「重新加载」
- **Host 校验**：`Host` 必须是 `127.0.0.1` / `localhost`，否则 403（防 DNS rebinding）
- **Origin 校验**：带 `Origin` 且不是 `chrome-extension://` 的一律 403（挡网页 JS）
- **命令 id 改用 UUID**：原来是自增整数，网页能盲猜 id 伪造 `/result`
  （把任意文本冒充成页面内容，prompt injection 的入口）。现在只接受仍在 pending 里的随机 id
- `/status` 也需要令牌

### P3 补回「先核对人」的硬规则

`SKILL.md` 硬性规则第一条 + 文献章节开头：**任何涉及具体学者的请求，第一步必须列 2-3 个
候选（姓名/机构/代表作/被引）让用户确认，不许自己拍板**。

### P2 权限提示

安装器打印加载扩展的步骤时，明确写出：扩展可读取并操作**所有网站**的页面内容
（`chrome.scripting` 的必要条件），只连 127.0.0.1，不外发数据。

### P4 / P5

- `type` 的提交改为 `keydown` + `keypress` + `keyup` 三事件，并优先用
  `form.requestSubmit()`（原实现只发 `keydown`，监听 `keyup` 的站点不触发提交）
- `snapshot` 的 ref 从页面 DOM 属性 `data-bh-ref` 改为**隔离世界里的 Map**
  （`globalThis.__bhRefs`）：不再修改用户页面、页面脚本也读不到 ref；
  元素被移除时 click/type 会明确报「请重新 snapshot」

### 验证

- 鉴权 8 项断言全过（无令牌/错令牌/Host 伪造/Origin 伪造/POST 缺头）
- 桥协议自检 12 项、冒烟 5 项全绿
- 真机 Edge 交互测试通过（snapshot → type → click → 读回 `submitted:机械臂`）

## 0.6.3 — 保证「换台机器也能装上交回规则」

0.6.2 修好了「手册没装」的问题，但装到别人的机器上还有两个坑，这一版补掉：

- **技能路径跟随 `DSH_HOME`**：之前写死 `~/.dsh`，自定义了 `DSH_HOME` 的用户会装到
  错误目录，DSH 扫不到。现在统一用 `<dshHome>/skills/browser-handoff/SKILL.md`；
  桥服务和扩展的安装目录也一并跟随
- **装完立刻回读校验**：frontmatter 不完整、或缺「撞墙就交给用户」这条规则时，
  安装直接 `SystemExit` 报错退出 —— 不再出现「装完了但模型读不到」的半成功状态
- **`--check` 把手册算进结论**：缺失或内容不完整时退出码 1，且明确提示重跑安装器；
  通过时打印「交回规则已就位」
- 已验证：自定义 `DSH_HOME` + 路径含空格/中文可正常安装；故意写坏手册会让安装失败

## 0.6.2 — 修复：遇到付费墙不交回用户

一次真实验证暴露了根因：**安装器从来没有把 `SKILL.md` 装进 DSH**，
所以模型既不知道"撞墙要交回用户"的规则，也不知道有 `lit_fetch.py` 这套流程，
只能自己用 `web_fetch` + Playwright 连撞十几个站点，最后写一份"我下不到"的报告。

- **安装器新增第 4 步**：把 `SKILL.md` 复制到 `%USERPROFILE%\.dsh\skills\browser-handoff\`，
  DSH 会把它识别成技能（`browser-handoff`），模型在新会话里就能看到规则
- `--check` 新增一行「使用手册 (skill)」，缺失时明确提示重跑安装器
- `--uninstall` 会一并移除这份手册（只删自己的目录）
- `SKILL.md` 新增一节「撞墙就交给用户，别自己绕」：判定条件（403 / Cloudflare /
  Purchase PDF / Sign in / OA 状态 CLOSED）、两步交回动作、换源最多 1 次、
  禁止用长报告代替交回、禁止镜像站
- `open_url` 的工具描述也写明了"用它把页面交给用户"
- 版本号同步到 0.6.2

## 0.6.1 — 清掉作废的思路

功能一个没减，只删掉上游遗留的、已经不适用的写法：

- 删掉 `install_bridge()` 这层纯转发包装（原本是给 macOS/Windows 双平台分派留下的）
- 删掉桥服务里没用到的 `socket` 导入
- README 去掉「路径 A / B / C 三条路」的并列叙事，改成「主路径 + 两个可选用法」
- `docs/gotchas.md` 第 1 条从 macOS 的 `seatbelt`/`bash` 表述改成 Windows 的受限令牌/`pwsh`
- `SKILL.md` 去掉「通过 bash 调用」「三条安装路径」这类过时说法

## 0.6.0 — 可以在用户当前页面上操作了

macOS 版只能「读」用户正在用的浏览器（点击、输入被 AppleScript 权限挡住）。
Windows 的扩展桥没有这个限制：这一版把**读 + 写**都做齐了。

- 新增 `snapshot`：扫描当前标签页里的按钮/链接/输入框，给每个元素分配 `ref`（如 `e3`）
- 新增 `click {ref}`：按 ref 滚动到可见并点击
- 新增 `type {ref, text, submit}`：按 ref 填表，走原生 value setter + `input`/`change`
  事件（React / Vue 受控组件也认），`submit` 可顺带回车/提交表单
- 页面快照只收可见元素，最多 200 个；页面变化后 ref 失效，重新 snapshot 即可
- `bridge_selftest.py` 增加三个新工具的协议断言（共 12 项）
- 真实浏览器验证：在测试页上 `snapshot → type "机械臂" → click 提交`，
  页面状态变成 `submitted:机械臂`

已知边界：DOM 层操作不是"可信事件"，风控严格的站点（支付、强人机校验）可能不认；
需要可信输入时可以再上 `chrome.debugger`（CDP），代价是浏览器顶部会出现调试提示条。

## 0.5.1 — 纯 Windows 版

从 0.5.0 精简而来，去掉所有 macOS 相关内容，功能不变（三个通道全保留）。

- 删除 `servers/browser_bridge.py`（AppleScript 桥）
- `install_dsh.py` 去掉 darwin 分支和 macOS 路径探测；在非 Windows 上直接提示退出
- 文档去掉 Safari / AppleScript / TCC 相关段落；`docs/gotchas.md` 由 12 条精简到 10 条，
  换入两条 Windows 实测坑（缓存目录、浏览器只剩后台进程）
- 冒烟测试与配置块检查改用 Windows 路径，编译清单跟着更新
- `browser_probe.py` 的 node / cli 探测改为 Windows 位置

## 0.5.0 — Windows 浏览器桥（与 macOS 功能对齐）

macOS 版有第三个通道「读你正在用的浏览器」（`mcp__browser__*`），Windows 上一直是跳过的。
这一版把它补齐，四个工具的能力与 macOS 版一致。

- **新增 `servers/browser_bridge_win.py`**：Windows 版桥服务。MCP stdio 出口不变，
  内部改用「本地 HTTP + 浏览器扩展长轮询」，只监听 `127.0.0.1:8790-8799`
- **新增 `extension/`**：Manifest V3 扩展。用 `chrome.tabs` / `chrome.scripting`
  在用户**自己的 profile** 里执行，所以登录态、已开的标签页都能直接用
- **安装器不再跳过桥**：Windows 上一键复制服务与扩展、登记 `mcp-browser`、
  并打印「加载解压缩的扩展」的三步指引
- **新增 `scripts/bridge_selftest.py`**：不启动浏览器就能验证整条协议，
  已接入 `smoke_test.py`
- 修 **端口占用**：Windows 的 `SO_REUSEADDR` 允许两个进程绑同一端口，
  会出现「请求随机落到另一个桥」的怪现象；桥服务现已禁用地址复用
- 修 **扩展连错桥**：扩展原本只连第一个应答的端口，遇到残留桥就再也收不到命令；
  现在同时服务 `8790-8799` 上所有应答的桥
- 修 **service worker 休眠导致超时**：浏览器只剩后台进程时 MV3 扩展会被挂起，
  命令要等 30 秒闹钟才被取走。现在窗口/标签事件会立即唤醒轮询，
  桥的等待上限也从 25 秒提到 45 秒
- 修 **没有窗口时 `open_url` 失败**：`chrome.tabs.create` 在无窗口时抛
  `No current window`，现在会自动改用 `chrome.windows.create` 开一个窗口
- 修 **结果回传偶发丢失**：动作已执行但 `/result` 请求失败时，工具会误报
  「没有响应」；扩展现在会重试一次
- 修 **自检可能被真扩展干扰**：`bridge_selftest.py` 起的临时桥可能被真实浏览器
  扩展连上并抢走命令。自检实例现在带一次性标记，真实扩展一律被 403 挡在外面

参数差异（平台决定）：`browser` 在 Windows 上只接受 `edge` / `chrome`；
`get_text` 不需要任何浏览器开关（macOS 的 Chrome/Edge 需要开「允许 Apple 事件中的 JavaScript」）。

## 0.4.9 — Windows 实测后修复

在 Windows 10 25H2 + DSH 0.2.1-rc.5 上跑完验证，修了四个问题：

- **安装重试**：DSH 运行时 `package.json.lock` 被占用会抛 EPERM，导致首次安装失败。
  现在自动重试 3 次，仍失败则明确提示「先完全退出 DSH」
- **中文乱码**：输出被重定向时 Python 用 GBK。所有脚本启动时自动切 UTF-8
- **路径分隔符**：默认下载目录由 `~` 拼接改为 `os.path.join`，不再出现 `C:\Users\x/Downloads`
- **自检临时目录**：Windows 沙盒里的 TEMP 子目录会 EPERM，改为优先用当前目录，
  并对创建失败给出可操作的提示
- 闭源文献的【需要你操作】提示改走 stdout——PowerShell 会把 stderr 渲染成红色错误
- `docs/windows-check.md` 补「已知的 Windows 坑」：python 占位符、锁、乱码、截图路径限制

## 0.4.8

- 新增 `docs/windows-check.md`：Windows 验证清单（6 步 + 失败时回传什么 + 已知不支持项）
- 无代码改动，仅补文档；版本号随之递增

## 0.4.7

**兼容性修复（重要）**

- 修 Python 3.9 崩溃：`str | None` 这类 PEP 604 注解在 3.9 上会直接抛 TypeError，
  导致 5 个脚本 import 阶段就挂。全部补上 `from __future__ import annotations`。
  macOS 自带的 `/usr/bin/python3` 正是 3.9——之前只在 3.12 上测过，是测试盲区。
- 冒烟测试的编译检查改为**内存 compile**，不再写 `.pyc`；沙盒或只读环境里也能跑通
- 修 `--set-format` 单独使用时被「必须给 --doi」挡住的问题

## 0.4.6

- **需要用户操作时直接提示**：`lit_fetch` 遇到闭源、`lit_triage` 列出清单后，都会打印
  「【需要你操作】」段落，写清下一步该点什么；SKILL 把这条列为硬性规则
- 新增 `QUICKSTART.md`（三分钟上手，可直接发给别人）
- SKILL 新增「文献工具怎么选」决策表：单篇 / 一批 / 全量 / 需登录 各用哪个

## 0.4.5

- 新增 `scripts/lit_triage.py`：批量 DOI 分流——免费的自动下载（HTTP，不碰浏览器），
  付费的写入 `needs-login.txt` 等用户点名，严格按约定的三层规则执行
- `lit_fetch.fetch_one()` 改为返回 `(路径, 原因)`，失败时能说清是「没有 PDF」还是「请求被拒」
- 修两个 bug：`filename_for` 缺作者字段时 IndexError；`fetch_one` 内层 return 少了元组包装

## 0.4.4

- SKILL 新增「付费文献：单篇、点名、人工登录」三层规则：
  A 用户自己下载（最稳）/ B 单篇按需下载（可接受）/ C 批量下载（禁止）
- 明确顺序：先用 OpenAlex 把免费的全拿到 → 列付费清单 → 用户点名 → 才开浏览器
- B 层写死边界：不循环、不遍历结果、每次间隔 ≥3 秒、遇风控立即停

## 0.4.3

- 新增 `scripts/parse_export.py`：解析用户导出的 RIS / BibTeX / WoS 制表符文件，
  自动去重（DOI 优先，其次标题）、按被引与年份排序，支持 `--latest / --json / --csv`
- SKILL 新增「闭源文献：用户导出，我解析」：把用户动作压到 3 步，其余全自动

## 0.4.2

- **格式询问移到最前面**：先问「PDF 还是 HTML」，10 秒不回答按 PDF；选择会持久化到
  `~/.dsh/lit-fetch.json`，之后不再问。`--set-format` 随时改
- 定了格式就不再中途二次询问：偏好 PDF 但只有网页版时直接报「未保存」（退出码 3）
- 非交互环境（脚本/agent 调用）自动按已记住的格式或 PDF 执行，不会卡住

## 0.4.1

- 新增 `scripts/lit_fetch.py`：自动下载**开放获取**文献。只从 Unpaywall / OpenAlex 的
  `best_oa_location` 与页面 `citation_pdf_url` 取文件，取不到就拒绝，不回退镜像
- 格式策略：默认只要 PDF；只有网页版时**不擅自保存**，提示并以退出码 3 结束（= 询问用户）
- 文件名规范：`年份-第一作者-标题.pdf`，按词边界截断

## 0.4.0

**定位调整：文献检索只走开放 API，浏览器只做合法的事。**

- 新增 `scripts/lit_search.py`：OpenAlex + Crossref + Unpaywall 三源，输出 `OA` / `CLOSED`
  并给出合法的 OA 副本链接；支持 `--json`、`--oa-only`、429 退避
- 删除 `paper_lookup.py`（功能被 lit_search 覆盖）
- SKILL 重写文献相关章节：
  - 检索流程改为「定位人 → 用户确认 → 拉列表 → 判 OA → 合法取全文」
  - 新增「绝对不能做的事」表：抓 WoS/Scopus/知网、抓 Google Scholar、Sci-Hub、绕付费墙、批量下载
  - 新增「涉及用户个人信息时」：检索人名前必须先让用户确认
  - 翻页技巧降级为「仅限允许抓取的站点」，不再用于订阅数据库

## 0.3.8

- **新增 `scripts/paper_lookup.py`**：用 OpenAlex 判断一篇文献是 `OA` 还是 `CLOSED`，
  带 429 退避重试与 `OPENALEX_MAILTO` 礼貌池；CLOSED 才提示走浏览器
- SKILL 新增三节：文献检索标准流程（OpenAlex 优先 → 浏览器兜底）、网页列表翻页、人物核查
- SKILL 新增「合规边界」：批量下载 / 绕付费墙 / 自动化访问的界线

## 0.3.7

- `smoke_test.py` 支持 `--browser chrome|msedge`：装完 Edge 通道后可以单独验证
  「Playwright 能不能真的拉起 Edge」——实测通过（`[msedge] 总体：通过`）
- 自检标签会显示当前测的是哪个浏览器，避免误读

## 0.3.6

- **新增 `scripts/smoke_test.py`**：一条命令跑完编译检查、配置块生成、浏览器自检、本机安装状态
  （`--quick` 跳过浏览器部分）；结束时自动清理 `__pycache__`
- gotchas 第 12 条：虚拟滚动列表「节点存在但 innerText 为空」的识别与收集方法
- 实测记录：用 Edge 通道完成 WoS 登录 + 检索 + 50 条结果抓取（全程无鼠标点击）

## 0.3.5

- **新增 `--self-test`**：一条命令验证整条链路（起浏览器 → 工具列表 → 页面渲染 → JS 执行），
  装完不用猜「到底通没通」
- 安装器结束时提示先跑自检
- 修自检本身的一个断言错误（`document.title` 在 data: 页面为空，改为校验 title + body 文本）
- 自检用临时目录跑，结束即删——不会在包里留下几十 MB 的 Chrome profile

## 0.3.3

- **卸载变可靠**：不再依赖备份文件；按标记把 playwright / browser / edge 三条通道
  一起截断，用户自己的配置原样保留（含单测：假 DSH_HOME 上验证过）
- **平台守卫**：浏览器桥依赖 AppleScript，非 macOS 直接跳过并说明，不再写入注定失败的配置
- `--check` 增加通道存在性检查（主通道 / 桥 / Edge）
- 新增本文件

## 0.3.2

- **新增 Edge 通道**：`--with-edge` / `--all`，用 Playwright 启动**真实的 Microsoft Edge**
  （独立 profile，登录态持久），工具名 `mcp__edge__*`
- **修正行为规范**：SKILL 原来写「用户说用 Edge 不能照字面执行」，正是 agent 自作主张
  换方案（改去调 API）的根源。改为：先按用户指定的浏览器执行，通道确实不存在时才说明边界
- 新增「不点击也能操作」的 JS 范式（赋值 + 派发事件 + 提交表单）

## 0.3.1

- 修 `open_url` 在 Chromium 上必失败的问题：`set active tab index` 改用 `open location`
- SKILL 增加「用户点名某个浏览器时」与学术数据库注意事项

## 0.3.0

- `--extract`：自动滚动整页、逐屏收集文本、去重存盘（对付 canvas 渲染的在线文档）
- SKILL 增加 canvas 文档的三步诊断；gotchas 第 9 条

## 0.2.0

- `servers/browser_bridge.py`：读取用户正在用的 Safari / Chrome / Edge
- `--with-bridge` 安装开关
- `get_text` 空结果自动重试 3 次

## 0.1.4

- `--check` 体检模式（版本对齐 + 配置完整性）

## 0.1.3

- 安装器自动读取 `dsh --version`，安装同版本插件（修 DSH 升级后工具全消失）

## 0.1.2

- SKILL 增加「站点适配表」：扫码 / 弹层 / iframe / 通用四种登录探针

## 0.1.1

- 修自定义 `--login-check` 恒为「已登录」、`--wait-login` 缺 `--url` 空等、安装器状态误报

## 0.1.0

- 首个版本：Playwright MCP 接线、登录交接流程、三种安装路径
