# browser-handoff

> **只下载开放获取内容。** 付费文献由你自己登录、单篇按需获取；本项目不抓数据库、不碰镜像站。
>
> 让 AI 助手在**真实浏览器**里查文献、下全文；遇到登录墙就停下来，把窗口交还给你。

一个给 AI 编码助手（DeepSeek Harness / Claude Desktop / Cursor / VS Code）用的技能包与命令行工具集。
它把「浏览器自动化」和「文献检索」缝在一起，并且**把合规边界写死在代码和规则里**。

---

## 它解决什么问题

| 场景 | 通常的做法 | 本项目的做法 |
|---|---|---|
| 论文在付费墙后面 | 抓取 / 找镜像 | **停下来，把窗口交给用户登录**，之后单篇按需取 |
| 在线文档是 canvas 渲染 | 复制不到文字 | 自动滚动整页、逐屏收集文本 |
| 只想确认有没有免费全文 | 先开浏览器再翻 | **先查 OpenAlex / Unpaywall**，OA 才下载，CLOSED 才开浏览器 |
| 数据库不让自动化 | 写爬虫（违约） | 让用户导出 RIS/BibTeX，程序负责解析、去重、排序 |

---

## 两套浏览器通道

| 通道 | 机制 | 能力 |
|---|---|---|
| **自动化通道** | Playwright 启动独立 Chrome / Edge（各自 profile，登录态持久） | 导航、点击、输入、滚动、执行 JS、截图、抓网络、登录交接 |
| **接管通道** | macOS：AppleScript；Windows：浏览器扩展 + 本地回环桥 | 读取**你正在用的**标签页与正文（Windows 版还能点击、输入） |

两条通道互补：自动化通道负责"干活"，接管通道负责"看你现在在看什么"。

---

## 文献检索：本项目最值得说的部分

传统做法是「先开浏览器 → 撞墙 → 手动登录 → 重复」。
这里的顺序反过来：**先用开放 API 把能免费拿的全部拿完，再决定要不要麻烦人。**

### 四个工具，一条流水线

```bash
# 1. 检索：判断一篇/一批文献是不是开放获取
python3 scripts/lit_search.py --author "Jane Smith" --limit 10
python3 scripts/lit_search.py --doi 10.1371/journal.pone.0000308

# 2. 下载：只从合法 OA 来源取，校验文件是真的 PDF
python3 scripts/lit_fetch.py --doi 10.1371/journal.pone.0000308

# 3. 分流：一批 DOI，免费的自动下，付费的列清单
python3 scripts/lit_triage.py --dois-file dois.txt --download

# 4. 解析：你从数据库导出的文件，变成干净表格
python3 scripts/parse_export.py --latest
```

`lit_triage` 的实际输出：

```
  [1/3] OA      10.1371/journal.pone.0000308
  [2/3] CLOSED  10.1126/science.aaq0216
  [3/3] OA      10.1371/journal.pmed.0020124

下载 2 篇开放获取...
  ✅ 2007-Piwowar-Sharing-Detailed-Research-Data-Is-Associated-with.pdf
  ✅ 2005-Ioannidis-Why-Most-Published-Research-Findings-Are-False.pdf

【需要你决定】这些没有免费副本，只能走机构权限：
  - 10.1126/science.aaq0216  Reducing food's environmental impacts...
  告诉我哪几篇要拿（1–3 篇我逐篇帮你开窗登录）。
```

### 具体提升了什么

1. **省掉大部分登录**
   OpenAlex + Crossref + Unpaywall 先判 OA。实测同一批文献里，多数能直接下载，
   真正需要机构权限的只占少数——不再"为了两篇付费文献打开浏览器翻十页"。

2. **批量处理有分流，不是一刀切**
   免费的走 HTTP 直连（不碰浏览器），付费的写进 `needs-login.txt` 等用户点名。
   浏览器只承担"少量、明确、用户授意"的下载。

3. **导出文件不再手工整理**
   `parse_export.py` 支持 RIS / BibTeX / WoS 制表符，自动按 DOI 去重、按被引与年份排序，
   可输出表格或 JSON。数据库不允许自动化，但**用户自己导出完全合法**——这条缝接上了。

4. **合规边界内建，不靠自觉**
   - 只从 Unpaywall / OpenAlex 标注的合法 OA 位置取文件，取不到就**拒绝**，不回退镜像
   - 付费文献只做「单篇、按需、人工登录」，不循环、不遍历结果
   - 明确禁止：抓 Web of Science / Scopus / 知网 / Google Scholar、Sci-Hub、批量下载
   - 涉及具体学者时，**先列候选让用户确认身份**，避免同名认错人

5. **格式策略可预计**
   第一次问「PDF 还是 HTML」，10 秒不回答按 PDF，选择被记住；
   定了格式就照做，不会中途再问一次。

---

## 安装

### macOS

```bash
cd macos
python3 install/install_dsh.py --all      # 主通道 + Edge 通道 + 浏览器桥
# 完全退出 DSH 再打开（⌘Q，不是关窗口）
python3 scripts/smoke_test.py             # 四项全绿即可用
```

### Windows

```bash
cd windows
python install\install_dsh.py --all
# 按提示在 edge://extensions / chrome://extensions 加载解压缩的扩展
python scripts\smoke_test.py
```

详细步骤见各自目录下的 `QUICKSTART.md` 与 `docs/`。

---

## 目录结构

```
macos/     macOS 版（0.5.0）——Playwright + AppleScript 桥
windows/   Windows 版（0.7.2）——Playwright + 扩展 + 本地回环桥
```

每个目录都是**完整可独立安装**的包：`install/` 安装器、`scripts/` 工具、`servers/` 桥服务、
`SKILL.md` 给 agent 的行为规范、`docs/` 踩坑记录。

---

## 已知限制

| 项 | 说明 |
|---|---|
| Firefox | 不支持：没有可用的 AppleScript 字典，也不接受外部 CDP |
| Safari 自动化 | 只能读取，不能点击输入（AppleScript 无对应语义） |
| 登录态 | 自动化通道使用独立 profile，每个站点首次需人工登录一次 |
| canvas 文档 | 能抓全文，但文字层常缺空格（`Thetransitiontowards...`）；要精确引用请导出原文件 |
| 权限 | Windows 扩展拥有 `<all_urls>`，可读写所有网站页面（`chrome.scripting` 的必要条件） |

---

## 合法使用（重要）

本项目的目标是**在合法范围内提升效率**，不是绕过限制。以下边界写死在代码与 `SKILL.md` 里：

### 会做的

| 行为 | 依据 |
|---|---|
| 只从**开放获取**来源下载全文 | Unpaywall / OpenAlex 标注的出版社 OA、机构仓库、预印本 |
| 付费文献由用户**本人登录**、**单篇按需**获取 | 用户行使自己的订阅权限 |
| 批量场景由用户从数据库**导出**，程序只做解析 | 导出是数据库自带功能，属授权行为 |
| 调用开放 API | OpenAlex、Crossref、Unpaywall、PubMed、arXiv 均明确允许程序化访问 |

### 不会做的

| 行为 | 原因 |
|---|---|
| 抓取 Web of Science / Scopus / 知网 / Google Scholar | 服务条款禁止自动化访问 |
| 访问 Sci-Hub 或任何镜像 | 侵犯著作权 |
| 绕过付费墙、伪造机构身份 | 违法 |
| 批量下载或系统性抓取订阅内容 | 违反订阅协议，可能牵连机构 IP |

### 风险提示

即使只下载单篇，多数订阅协议仍可能将"自动化访问"视为违约。**请以你所在机构的许可条款为准**；
本项目不提供法律意见，使用者需自行确保其使用方式符合订阅协议与当地法律。

### 其他安全设计

- 桥服务只监听 `127.0.0.1`；Windows 版带共享令牌 + Host/Origin 校验
- 浏览器产物只能写到指定输出目录
- 详见各包 `SKILL.md` 的「绝对不能做的事」与 `docs/gotchas.md`

---

## 来源与许可

- `macos/`：本项目主线实现
- `windows/`：同一项目的 Windows 实现（扩展 + 本地桥），设计差异见 `windows/PROVENANCE.md`

代码以 MIT 许可发布，见 [LICENSE](LICENSE)。
