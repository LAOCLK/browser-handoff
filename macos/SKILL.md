---
name: browser-handoff
description: >
  Drive a real, visible Chrome through Playwright MCP, and hand control to the
  human whenever a page needs login (or 2FA / device verification / captcha).
  Use when a task requires reading a page that is behind a login wall, when a
  site blocks plain HTTP fetching, when the user asks to see the browsing
  process, or when the user says 需要登录 / 打不开 / 你来点 / 演示一下.
  Also covers literature work: 查文献、找某位学者的论文、下载全文（仅开放获取）、
  解析数据库导出文件.
metadata:
  version: "0.5.4"
  license: "MIT"
---

# Browser handoff

一套「agent 开浏览器 → 撞到登录墙 → 人接管登录 → agent 继续」的协作流程。

## 前提：确认工具在不在

如果你的工具表里有 `mcp__playwright__*`，用它们。没有的话看 `README.md` 的三条安装路径；
实在装不上，用 `scripts/browser_probe.py`（免安装，通过 bash 调用）。

## 两条通道，别混用

| 通道 | 工具前缀 | 什么时候用 |
|---|---|---|
| **Playwright** | `mcp__playwright__*` | 需要**操作**：点击、输入、多标签、抓取数据、登录交接 |
| **浏览器桥** | `mcp__browser__*` | 需要**接入现场**：读用户正在用的 Safari/Chrome/Edge 的标签和正文 |

浏览器桥只能读（外加开新标签），不能点击输入。它的价值在于「用户已经开到那一页了」——
直接读，不用让用户复制链接，也不用另开一个浏览器重新登录。

### 浏览器桥的用法

```
mcp__browser__list_tabs   {browser: "safari"|"chrome"|"edge"}
mcp__browser__current_tab {browser: ...}
mcp__browser__get_text    {browser: ...}
mcp__browser__open_url    {browser: ..., url: "https://..."}
```

能力不对等，**先看浏览器再选方法**：

| | Safari | Chrome / Edge |
|---|---|---|
| 列标签 / 当前标签 / 开标签 | ✅ | ✅ |
| 读正文 | ✅ **无需任何设置**（走 `text of document`） | ⚠️ 必须开启「允许 Apple 事件中的 JavaScript」 |
| 点击 / 输入 | ❌ | ❌ |

- Safari 读正文不需要开关，因为 `text of document` 是文档模型属性，不走 JS 引擎。
- Chrome/Edge 没有等价属性，只能 `execute javascript`，被开关挡着就会返回明确提示。
- Firefox 没有可用的 AppleScript 字典，不支持。
- 刚 `open_url` 或刚切标签时页面可能还没渲染完，`get_text` 会自动重试
  3 次（每次间隔 0.8 秒）；仍为空会提示「可能还在加载」，重试即可。

### 什么时候用桥、什么时候用 Playwright

- 用户说「看看我现在这个网页」→ `mcp__browser__current_tab` / `get_text`
- 用户说「帮我打开这个链接」且他们就在浏览器前 → `mcp__browser__open_url`（用他们自己的登录态）
- 需要点击、填表、翻页、抓一堆数据 → Playwright
- 需要登录交接 → Playwright（它自己开窗，你控制得了）

## 用户点名「用某个浏览器」时

用户说「**用 Edge 打开 X，搜索 Y**」——现在这句话**可以照字面执行**，只要对应通道在：

| 用户说 | 用这个 |
|---|---|
| 用 Edge | `mcp__edge__*`（Playwright 启动的真实 Microsoft Edge） |
| 用 Chrome | `mcp__playwright__*`（Playwright 启动的 Chrome） |
| 在我现在开着的浏览器里看 | `mcp__browser__*`（只读：标签、正文、开新标签） |
| 我只是要数据 | 直接调开放 API，浏览器都不用开 |

`mcp__edge__*` 和 `mcp__playwright__*` 是同一套 25 个工具，只是底层浏览器不同。
两者都用**独立 profile**（登录态各自持久），所以"用 Edge"意味着"打开一个真实的 Edge 窗口"，
而不是接管用户已经开着的那个 Edge。

**仍然做不到**的是接管用户**当前**那个窗口（TCC 挡住调试端口）。遇到这种情况说明清楚，
别去试这些死路（实测全部失败）：

- `tell application "System Events" to click ...` → `-10004 权限违例`
- 已开窗口的 `execute javascript` → 需要用户手动开「允许 Apple 事件中的 JavaScript」
- 读 `~/Library/Application Support/Microsoft Edge` → TCC `Operation not permitted`

**行为要求**：先按用户指定的浏览器执行；只有该通道确实不存在时，才说明边界并给替代，
不要自作主张换方案（这是真实事故：用户说"用 Edge 搜 WoS"，agent 试了十几次 AppleScript 后
改用 API，用户拿到的不是他要的东西）。

### 不点击也能操作

搜索、填表不需要模拟鼠标：`browser_evaluate` 里直接赋值并提交更稳。

```js
() => {
  const input = document.querySelector('input[type="search"], input[name="q"]');
  if (!input) return 'no input';
  input.focus();
  input.value = 'AU=(Smith J)';
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
  const form = input.closest('form');
  if (form) form.submit();
  return 'submitted';
}
```

分页同理：直接改 URL 参数跳下一 页，比点"下一页"按钮可靠。

### 学术数据库特别注意

- **Web of Science / Scopus / 知网**都需要机构订阅：打开会跳到 `access.clarivate.com` 之类的登录页，
  我这边没有你的机构账号，**必然卡住**。
- 要么让用户在 Playwright 的窗口里登录一次（之后持久），要么直接换开放数据源：
  - OpenAlex `https://api.openalex.org/works?filter=author.id:...`（免密钥）
  - Crossref `https://api.crossref.org/works?query.author=...`
  - PubMed E-utilities（生物医学）
  - ORCID `https://pub.orcid.org/v3.0/<id>/works`（作者作品列表）
- 换数据源不是"降级"——这些接口返回的是结构化元数据，比网页抓取干净得多。

## 读整页，尤其是 canvas 渲染的文档

有些站点（网盘文档、在线 Office、PDF 阅读器）的正文**不在 `innerText` 里**，而是画在
canvas 上，只把当前视口那几页的文本放进 DOM。表现就是：能读到工具栏和大纲，读不到正文；
用户会觉得"你得让我一页一页翻"。

诊断三连（`mcp__playwright__browser_evaluate`）：

```js
() => ({
  innerTextLen: document.body.innerText.length,          // 1. 正文在 innerText 里吗
  canvases: document.querySelectorAll('canvas').length,  // 2. 是不是 canvas 渲染
  hasTermInHtml: document.documentElement.innerHTML.includes('正文里的某个词')  // 3. 隐藏文字层
})
```

如果第 3 条命中但第 1 条没有 → **有隐藏文字层，滚动就能收集到**。用 `--extract`：

```bash
python3 scripts/browser_probe.py --url "<地址>" --extract out.txt
```

它会滚动整页、逐屏收集 `document.body` 下的文本节点、去重后写入文件。
等价的手写版是 `scripts/browser_probe.py` 里的 `EXTRACT_JS`，可以直接复制到
`browser_evaluate` 里用。

**两个必知的坑**：

1. **canvas 文字层常常没有空格**。抽出来是 `Thetransitiontowards...`，能读懂但不适合引用。
   要精确文本，优先让用户导出 docx/pdf；否则只能用 OCR（截图 + 视觉模型）。
2. **只加载当前视口的站点**必须滚动才能拿全。一次滚到底再收集是拿不到中间页的，
   要边滚边收（`--extract` 就是这么做的）。

**更优先的路**：如果那一页的数据是接口返回的（JSON），别抽 DOM——
用 `browser_network_requests` 找 XHR，再用 `browser_network_request` 取 `response-body`，
拿到的就是原始数据，没有渲染层的任何损失。前提是请求发生在你这次打开页面之后。

## 工具突然消失（DSH 升级之后）

**现象**：昨天还有 `mcp__playwright__*`，今天一个都没有，调用返回 `unknown tool`。

**原因**：DSH 本体升级后，插件版本没跟上，加载**静默失败**——不报错，只是工具不出现。

**修复**（第二步必须由人做）：

```bash
python3 install/install_dsh.py --check   # 体检：退出码 1 表示需要修
python3 install/install_dsh.py           # 自动装成与 DSH 同版本的插件
```

然后**完全退出 DSH 再重开**（插件只在启动时加载，无法热更新）。

注意：

- 不要自己改 `package.json` 里的版本号，也不要先 `--uninstall`——安装器是幂等的，直接重跑即可
- 发现工具缺失时先跑 `--check` 确认，再让用户重启；**不要**改用 HTTP 抓取硬凑，
  那是另一条路径，会丢掉登录态

## 遇到墙：先交回，再谈失败

付费墙、登录墙、人机验证，都是**必须由人来过**的地方。判定到任一条，立刻停止自己找替代：

- 页面出现密码框、二维码、设备验证，或「Sign in / 登录 / 注册 / Log in」
- URL 跳到 `/login` `/signin` `/session` `/passport` `/auth`
- 正文为空且页面写着「订阅后可读」「机构访问」「Access denied」「请先登录」

交回只有两步，做完就**停下等回复**：

1. 把窗口停在那张页面上——不要关，也不要继续导航
2. 第一句就写「**需要你操作：** 登录 / 机构订阅 / 购买 / 过验证」，并说清做完后要回你什么

硬性约束：

- 换源最多再试 **1 个**（例如出版社页 → 作者机构库），仍然不行就交回
- **不许用「我找不到」的长报告代替交回**——报告可以写，但必须先把页面交到用户手上
- **不许**把登录墙说成「该网站无法访问 / 抓取失败 / 没有权限」；那是"需要你登录"，不是失败
- 文献任务优先跑 `lit_fetch.py --doi <DOI>`——它内置同一套逻辑（闭源时打印
  【需要你操作】并以退出码 2 结束），比现场拼流程可靠

### 开窗口是预授权的，不要问

用户装这个插件就是为了让你把登录页弹出来。
「需要我帮你打开浏览器吗？」「要不要我逐篇打开？」这类反问等于把球踢回去，
是这类任务里最常见、也最让人恼火的失败方式。**直接开，开完再说话。**

### 报告前必须先开页面

任何包含「需要机构登录 / 需要订阅 / 需要付费 / 需要过验证」的回复，
在它之前必须已经真实调用过 `browser_navigate`（或 `open_url`）把那一页打开。
**只给清单、只给 DOI、只给「用校园网自己下」而不开窗口 = 流程失败，重做。**

### 批量场景也一样

一次检索出几十篇被订阅墙挡住时，**第一次判定到墙就立刻打开第一本被挡的那一篇**
（或出版社的机构登录入口），让用户先把登录做完；清单可以同时列，
但顺序永远是「**先弹窗，再报告**」。

标准动作长这样（照抄这个结构）：

> **需要你操作：** 我已经打开 *Water Research* 的机构登录页，
> 请在这个窗口里用校园账号登录（CARSI / 统一身份认证）。
> 登录后页面会跳回那篇文章，回我一句「好了」，我接着取。

注意 macOS 的特殊之处：**登录必须在 Playwright 的窗口里完成**。
用 `mcp__browser__open_url` 打开的是用户自己的浏览器——他能登录，但你接不上下文。
要接着干活就一律用 `mcp__playwright__browser_navigate` 开窗。

## 核心循环

1. `mcp__playwright__browser_navigate` 打开目标页
2. `mcp__playwright__browser_evaluate` 跑下面这个探针，判断是否被登录墙拦住：

```js
() => { const m = document.querySelector('meta[name=user-login]');
  return JSON.stringify({url: location.href,
    pwd: !!document.querySelector('input[type=password]'),
    user: m ? m.content : null, title: document.title}); }
```

3. **判据（三条同时成立才算登录成功）**
   - `pwd == false`（没有密码输入框）
   - URL 里没有 `/login` `/signin` `/session` `/passport`
   - `user` 非空 **或** 连续两次检测结果稳定

   只满足前两条不算数——设备验证页、扫码页都没有密码框，但都还没登录。
4. 如果有墙：**停下来**，明确告诉用户「窗口已打开，请完成登录，登完我就继续」。
   不要自己去输账号密码，也不要尝试绕过。
5. 每 5 秒重复第 2 步，最多等 10 分钟。检测到登录成功后继续原任务。

## 硬性规则

- **浏览器产物（截图/下载）必须写到 `--output-dir` 指定的目录**——MCP 只允许往
  指定的根目录写，其他绝对路径会被拒（报 `outside allowed roots`）。
  默认在 `~/browser-handoff-artifacts`。
- **截图/导出文件必须用绝对路径**。DSH 里 MCP server 的工作目录是 profile 目录
  （`~/.dsh/profiles/<name>/`），相对路径会写进那里而不是工作区。
- 等待期间**不要**导航到别的地址，那会打断用户的登录流程。
- **需要用户做事时，第一句就写「需要你操作：…」**，不要埋在段落后面
- 关浏览器用 `browser_close`；用完就关，别留窗口。
- 登录态保存在 `--user-data-dir` 指定的 profile 里，所以**同一个站点只登一次**。
  不要为了「保持登录」去复制用户的日常 Chrome profile——两个进程抢一个 profile 会互相破坏。

## 常见失败模式

| 现象 | 原因 | 处理 |
|---|---|---|
| `sandbox initialization failed` | Chrome 沙盒套在容器/沙盒里无法嵌套 | 加 `--no-sandbox` |
| `EPERM: mkdir .../ms-playwright` | 进程无权写缓存目录 | 把 `HOME` / `PLAYWRIGHT_BROWSERS_PATH` 指到可写位置 |
| 页面 403 / 「请求存在异常」 | 可能是风控，也可能是「未登录」 | 先看有没有登录入口：**有 → 交回**；纯风控页 → 最多换 1 个信源，仍不行就交回 |
| 页面空白、`url=?` | 跳转中的瞬时状态 | 当墙处理，多等一轮 |
| 工具报「No open tabs」 | 页面已被关闭 | 重新 `browser_navigate` |

## 站点适配表

不同站点「登录完成」的样子不同。默认探针只对 GitHub 精确，其余按下表换成站点专用探针。

约定：`--login-check` 的函数 **返回 `true` 表示「还没登录」**，返回 `false` 表示可以继续。

| 站点类型 | 默认探针表现 | 换成 |
|---|---|---|
| GitHub 类（页面有 `meta[name=user-login]`） | 精确 | 默认即可 |
| 账号密码登录（会跳转） | 可靠 | 默认即可 |
| **扫码登录**（二维码，页面没有密码框） | 误判成「已登录」→ 立刻放行 | 检测二维码元素是否还在 |
| **弹层登录**（URL 不变，浮一层遮罩） | 误判成「已登录」 | 检测遮罩/弹窗是否还在 |
| **iframe 内登录**（登录框在子框架里） | 主文档看不到密码框 | 检测 iframe 是否还在 |
| 需点击才弹出登录框 | 首次检测过早 | 先 `browser_click`，再等待 |

**扫码登录**

```js
() => !!document.querySelector('img[src*="qr"], canvas, .qrcode, [class*="qrcode"]')
```

**弹层登录**

```js
() => !!document.querySelector('[role="dialog"], [class*="login-modal"], [class*="mask-layer"]')
```

> 弹层探针容易误报（很多正常页面也有 `.modal` 这类类名）。先用
> `browser_evaluate` 手动跑一次看返回值，再拿它当判据。

**iframe 登录**

```js
() => !!document.querySelector('iframe[src*="login"], iframe[src*="passport"], iframe[src*="signin"]')
```

**通用兜底（不依赖任何站点特征）**

```js
() => {
  const pwd = !!document.querySelector('input[type=password]');
  const bad = /\/login|\/signin|\/passport|\/session/i.test(location.href);
  return pwd || bad;
}
```

命令行用法：

```bash
python3 scripts/browser_probe.py --url "https://example.com" --wait-login 600 \
  --login-check "() => !!document.querySelector('img[src*=\"qr\"]')"
```

在 agent 里用 `mcp__playwright__browser_evaluate` 时，把同样的表达式直接传给 `function` 参数，
读返回的 `true` / `false` 即可，不需要自己再写 JSON 包装。

**调试顺序**：先 `browser_evaluate` 跑探针 → 确认它在「登录前」和「登录后」返回值确实相反
→ 再把它接进等待循环。跳过这一步，就等于赌站点结构。

## 被人机验证挡住时

自动化浏览器会被部分站点判定为异常流量，弹出一个不可关闭的对话框：

> There is unusual activity coming from your institution. Please verify you are human to proceed.

**检测**（`browser_evaluate`）：

```js
() => {
  const d = [...document.querySelectorAll('[role=dialog], .cdk-overlay-pane, mat-dialog-container')]
    .map(x => (x.innerText || '').replace(/\s+/g, ' '));
  return JSON.stringify({
    walled: d.some(t => /verify you are human|unusual activity|captcha|人机验证|异常流量/i.test(t)),
    dialogs: d.slice(0, 2)
  });
}
```

**规则**：

1. 检测到就**停**，把窗口交给用户完成验证 —— 这是人工步骤，绕不过去。
2. **不要**反复点击或重复提交，那会让判定更重。
3. 用户确认完成后，重新检测一次对话框是否消失，再继续原任务。
4. 这类验证和登录是两回事：**登录态解决不了它**。`--wait-login` 的等待逻辑可以复用，
   只是把「已登录」判据换成「对话框消失」。

**同类情况**：Cookie 同意弹层、语言选择弹层、新功能引导。它们也会吃掉点击和回车
（表现为"输入了但没反应"）。先查有没有遮罩，再判断是不是真的在操作页面——
这是排查"点了没反应"的第一顺位。

## 文献检索：只走开放 API

> **硬规则：任何涉及具体学者的请求，第一步必须列候选并让用户确认身份。**
> 哪怕你觉得自己很确定。同名学者极多（实测某个常见中文名的英文拼写，在 OpenAlex 里就有 5 个不同的人），
> 一次猜错，整份结果全部作废。确认成本是一句话，返工成本是一整天。
>
> ```bash
> python3 scripts/lit_search.py --author "<姓名>"   # 先列候选，再让用户选
> ```


插件在文献这块的定位是**发现 + 合法获取**，不是爬数据库。

```
定位人（OpenAlex）→ 用户确认身份 → 拉作品列表 → 判断 OA → 合法取全文
```

```bash
python3 scripts/lit_search.py --author "Jane Smith" --limit 10
python3 scripts/lit_search.py --doi 10.1371/journal.pone.0000308
python3 scripts/lit_search.py --title "perfluorinated compounds wastewater"
```

- `OA`：有合法免费副本，直接取（出版社或机构仓库）
- `CLOSED`：没有免费副本 → 把 DOI 页面交给用户，由他用**自己的权限**访问

拿到 OA 之后直接下载（全自动，不需要用户操作）：

```bash
python3 scripts/lit_fetch.py --doi 10.1371/journal.pone.0000308
python3 scripts/lit_fetch.py --doi 10.1371/journal.pone.0000308 --out ~/papers
```

**格式策略**：先问一次「要 PDF 还是 HTML」——10 秒不回答就按 PDF。
选了会被记住（`~/.dsh/lit-fetch.json`），以后不再问；改主意用 `--set-format`。
定了格式就按格式执行，**不会中途再问第二次**：偏好 PDF 但只有网页版时，
直接报「未保存」并以退出码 3 结束。

设置 `UNPAYWALL_EMAIL` 可以拿到 Unpaywall 的 OA 链接（出版社 / 机构库），
比 OpenAlex 的 `oa_url` 精确，且都是合法副本。

## 绝对不能做的事（写死）

| 行为 | 结论 |
|---|---|
| 抓 Web of Science / Scopus / 知网 | ❌ 条款禁止自动化访问，属违约 |
| 抓 Google Scholar | ❌ 明确禁止 |
| Sci-Hub / 任何镜像站 | ❌ 侵权 |
| 绕付费墙、伪造机构身份 | ❌ 违法 |
| 批量下载全文、整库搬运 | ❌ 违反订阅协议（单篇按需≠批量，见下节） |

**允许的**：OpenAlex、Crossref、Unpaywall、PubMed E-utilities、arXiv、
Semantic Scholar、DOAJ、CORE —— 官方 API，明确鼓励程序化调用。

浏览器在文献场景只做两件事：

1. 打开 **OA 链接**，读 / 存（合法）
2. 打开**用户有权访问**的页面，**登录和下载由用户自己操作**，我只读他愿意读的部分

需要全量数据时，请用户用数据库自带的导出功能（WoS 允许一次导出 500 条，
在授权范围内），我来解析导出的文件。

## 闭源文献：用户导出，我解析

数据库不允许我自动化访问，但**用户自己导出完全合法**。把用户的动作压到最少：

**用户做（约 3 步，一次）**
1. 结果页 → `Select all`（或全选本页）
2. `Export` → 选 RIS / BibTeX / Tab-delimited
3. `Export` → 文件落到 `~/Downloads`

**我做（一条命令）**

```bash
python3 scripts/parse_export.py --latest
python3 scripts/parse_export.py savedrecs.ris --csv out.csv
```

自动找 `~/Downloads` 里最新的导出文件，解析、按 DOI/标题去重、按被引与年份排序。
`--json` 出原始数据，`--csv` 出表格。

**为什么压不成一个键**：WoS 没有「一键导出全部」的接口，选记录、选格式是两步 UI。
能自动化的是「导出之后」，不是「导出本身」——后者一旦由代码做，就回到违约。

## 文献工具怎么选

| 情境 | 用哪个 |
|---|---|
| 单篇，已知 DOI | `lit_fetch.py --doi <DOI>` |
| 只知道标题/作者 | `lit_search.py --title/--author` |
| 一批 DOI，或从导出文件来 | `lit_triage.py --download` |
| 几十上百篇要全量 | 用户导出 → `parse_export.py --latest` |
| 需要登录的少数几篇 | 浏览器，一次一篇（见下节） |

原则：**能 HTTP 就不开浏览器；能批量分流就不逐条手工。**

## 一批文献怎么办：先分流，再决定

```bash
python3 scripts/lit_triage.py --dois-file dois.txt --download
python3 scripts/lit_triage.py --from-export ~/Downloads/savedrecs.ris --download
```

它做三件事：

1. 逐个查 OpenAlex（间隔 0.4 秒，不并发）
2. **能免费拿的直接下**（HTTP，不经浏览器），文件名 `年份-作者-标题.pdf`
3. 拿不到的写进 `needs-login.txt`（DOI + 标题 + 原因）

所以顺序是：免费的一批一次性做完 → 清单交给你 → 你点名 → 才开浏览器一篇篇来。

## 付费文献：单篇、点名、人工登录

三层规则，从最安全到可接受：

| 层级 | 做法 | 判定 |
|---|---|---|
| **A 最稳** | 用户自己点下载，我只读屏幕上他也能看到的内容 | 无争议 |
| **B 可接受** | 用户登录后，我打开**那一篇**的 PDF 并保存 | 单篇、按需、人类节奏 |
| **C 禁止** | 遍历清单批量下载、循环抓同站文章 | 违约 |

B 的边界（写死，不许越）：

- 一次只处理**用户点名的一篇**，不预先批量打开
- 不循环、不遍历检索结果列表
- 操作之间保持人类节奏（间隔 ≥ 3 秒），不并发
- 下载完这一篇就停，不顺手抓同站其他文章
- 遇到验证码 / 风控，立即停手交给用户

**顺序也很重要**：

1. 先用 OpenAlex / Unpaywall 把**免费的**全部拿到（HTTP 直连，不经浏览器）
2. 免费的做完，再列出**需要登录的**清单给用户
3. 用户确认某几篇 → 才开浏览器，一次一篇

这样浏览器只承担"少量、明确、用户授意"的下载，不会演变成批量采集。

## 涉及用户个人信息时

- 检索某个人名 / 邮箱 / 机构之前，**先向用户确认**
- 同名作者很常见：先列候选（单位 / ORCID / h / 方向），让用户选
- 用户没确认前，不批量抓取，也不把推测当作事实

## 网页列表翻页（仅限允许抓取的站点）

适用于自己的后台、公开目录、站点明确允许抓取的列表——**不适用于订阅数据库**。

- WoS 结果页 URL 末段是页码（`/relevance/1` → `/relevance/2`），但**不要用它做批量抓取**
- 每页 50 条，DOM 只渲染视口附近；必须边滚边收（gotchas 第 12 条）
- 抓完核对条数，明显少于预期就重滚补漏

## 内容取舍

登录之后读用户**有权访问**的内容是正常的；绕付费墙、找镜像、伪造身份不做。
遇到「要订阅」的情况，直接告诉用户，并建议公开替代信源。

## 更快的手动路径

不想让模型每步都判断时，用脚本一次跑完：

```bash
python3 scripts/browser_probe.py --url "<地址>" --wait-login 600 --snapshot
```

它会自己发现有头浏览器、打开页面、检测登录墙、等你登录、然后输出快照。
