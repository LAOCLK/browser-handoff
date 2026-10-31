# 踩坑记录

这些都是实际调试时撞出来的，不是理论推演。

## 1. 嵌套沙盒：Chrome 起不来

**现象**

```
sandbox initialization failed: Operation not permitted
GPU process isn't usable. Goodbye.
```

**原因**：Chrome 自己会给渲染进程套一层沙盒。如果它本身已经跑在别人的限制里（容器、受限令牌、
部分公司安全策略），内核不允许这种嵌套，直接拒绝。

**解法**：加 `--no-sandbox`。

**代价**：Chrome 的渲染进程隔离被关掉。在「本来就已经被关进笼子」的环境里这是必要代价；在普通桌面环境里能不加就不加。

```
【沙盒里的调用】                        【宿主进程里的调用】
DSH 宿主（无沙盒）                      DSH 宿主（无沙盒）
└─ pwsh 派生的进程 ← 沙盒从这开始        ├─ 插件 dsh-mcp-client
   └─ MCP server                        │  └─ MCP server  ← 没有任何沙盒
      └─ Chrome ← 撞墙的在这里           │     └─ Chrome ← 沙盒正常工作
                                        └─ pwsh 派生的进程 ← 沙盒只罩这一支
```

限制沿进程树向下继承。同一个 Chrome，父亲不同，待遇完全不同。

## 2. 缓存目录不可写

**现象**

```
Error: EPERM: operation not permitted, mkdir '...\.cache\ms-playwright'
```

**原因**：Playwright 和 Playwright MCP 都要往缓存目录建文件。在受限进程或只读盘里写不了。

**注意**：缓存路径**由 `HOME` 推导**（Windows 上是 `%USERPROFILE%\.cache\ms-playwright`），
`XDG_CACHE_HOME` 对它无效。

**解法**：
- 普通桌面环境：不用管，本来就能写
- 受限环境：把 `HOME` 指到可写目录，或用 `PLAYWRIGHT_BROWSERS_PATH` 单独指定浏览器目录

## 3. 相对路径落到 profile 目录

**现象**：`browser_take_screenshot(filename="a.png")` 写出来的文件不在当前目录。

**原因**：MCP server 的工作目录是 profile 根目录（DSH 里是 `%USERPROFILE%\.dsh/profiles/<name>/`），不是你的工作区。

**解法**：**永远传绝对路径**，或者干脆不传 `filename`，让它落到 `--output-dir`。

## 4. 登录检测：否定信号会骗人

**现象**：检测到「密码框消失了」就以为登录成功，结果点进去还是登录页。

**原因**：登录是**多步**的。以 GitHub 为例：

```
/login  →  /session（提交账号密码）  →  /sessions/verified-device（设备验证）  →  /settings/profile
```

中间那两步都没有密码框，但都没登录完。

**解法**：只认**肯定信号**。GitHub 用的是 `<meta name="user-login">`，只有已登录页面才存在。没有这种信号时，退而求其次：URL 不含 `/login|/signin|/session` **且**连续两次检测结果稳定。

**通用写法**

```js
() => { const m = document.querySelector('meta[name=user-login]');
  return JSON.stringify({url: location.href,
    pwd: !!document.querySelector('input[type=password]'),
    user: m ? m.content : null}); }
```

## 5. 反自动化 ≠ 登录墙

**现象**：知乎、小红书这类站点直接返回

```json
{"error":{"message":"您当前请求存在异常，暂时限制本次访问。","code":40362}}
```

**原因**：对方识别出这是自动化浏览器（指纹层面），跟你登不登录无关。

**解法**：没有体面的办法。换信源，或者让人类自己去那个站点复制内容。**不要**在指纹对抗上投入——那是另一个问题，而且通常违反服务条款。

## 6. 登录态是 profile，不是魔法

cookie 存在 `--user-data-dir` 指定的目录里：

- DSH 配置里默认：`%USERPROFILE%\.dsh/playwright-profile`
- 脚本默认：`~/.browser-handoff-profile`
- Claude Desktop / Cursor：不传该参数，用 Playwright MCP 自带的持久 profile

删除该目录 = 退出所有站点。想「换个人用」就删它。

**不要**把用户日常的 Chrome profile 指过来——Chrome 会在两个进程间争抢同一个 profile，可能损坏数据。

## 7. 正文读不到？先分清是「没有」还是「没渲染」

在线 Office、网盘文档、PDF 阅读器这类站点，正文常常画在 **canvas** 上，DOM 里只放
当前视口那几页的文字。症状很典型：

- `document.body.innerText` 只有工具栏、大纲、页码，没有正文
- `document.querySelectorAll('canvas').length > 0`
- 但 `document.documentElement.innerHTML.includes('正文里的某个词')` 是 **true**

第三条命中说明**有隐藏文字层**——`innerText` 会跳过隐藏元素，`textContent` / 遍历文本节点
不会。这时候滚动整页、边滚边收集就能拿到全文。

两个陷阱：

1. **滚动到底再收集 = 白干**。虚拟滚动的站点在滚动过程中会卸载早期页面，
   必须一边滚一边收、按行去重。
2. **canvas 文字层通常没有空格**。同一个句子在 DOM 里是
   `Thetransitiontowardsmoresustainablefood`，因为空格是画出来的，不是字符。
   能读懂，但不能直接引用。要精确文本只有两条路：让用户导出原始文件，或者 OCR。

**优先级建议**：接口数据 > 导出文件 > DOM 文字层 > OCR。
接口优先是因为 `browser_network_requests` + `response-body` 拿到的就是服务端原始 JSON，
不经过任何渲染层。

## 8. 浏览器关掉窗口后只剩后台进程

**现象**：`mcp__browser__list_tabs` 返回「没有打开的标签页」，`current_tab` 返回「没有打开的窗口」，
但任务管理器里明明有 `msedge.exe`。

**真因**：Edge 关掉所有窗口后仍会保留后台进程（扩展还在跑），此时没有窗口、没有标签页，
不是插件坏了。

**解法**：
- 让用户打开浏览器窗口即可，标签页立刻就能读到
- `open_url` 在这种情况下会自动开一个新窗口（旧版本会报 `No current window`，已修）

## 9. 「点了没反应」先查遮罩，别怀疑选择器

**现象**：输入框能赋值、能触发 autocomplete，但按回车、点提交都没反应，URL 不变。

**真因**（实测 WoS）：页面上盖着一个 `role=dialog` 的人机验证弹窗——

> There is unusual activity coming from your institution. Please verify you are human to proceed.

`input.disabled === true`、`aria-expanded === "false"`，所有输入事件被吞掉。
不是选择器错了，是页面根本没在听。

**排查顺序**：

```js
document.querySelectorAll('[role=dialog], .cdk-overlay-pane, mat-dialog-container').length
```

非零就先看弹层内容，再谈操作。Cookie 同意、语言选择、新功能引导也会造成同样效果。

**处理**：人机验证必须由人完成。停手、把窗口交给用户、验证完重新检测，然后继续。
不要反复重试——重复的自动化行为会让风控升级。

## 10. 列表项「存在但为空」——虚拟滚动又来了

**现象**：`document.querySelectorAll('app-record').length === 20`，但只有第一条
`innerText` 有内容，其余全是空字符串。

**真因**：结果列表是虚拟滚动。DOM 里预留了节点，但没滚到视口的条目没有渲染文本。
（WoS 的结果页就是这样，实测 20 个节点里只有 1 个有内容。）

**解法**：和 canvas 文档一样——边滚边收，按索引去重：

```js
const seen = new Map();
const collect = () => document.querySelectorAll('app-record').forEach((r, i) => {
  const t = (r.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  if (t.length > 2 && !seen.has(i)) seen.set(i, t.join(' | '));
});
collect();
const box = document.scrollingElement;
for (let i = 0; i < 40; i++) {
  box.scrollTop = Math.min(box.scrollTop + 800, box.scrollHeight);
  await new Promise(r => setTimeout(r, 500));
  collect();
}
```

判据很简单：**节点数 ≠ 有内容的节点数**，就说明遇到了虚拟滚动。

## 11. 连续下载触发出版社限流（CPE00002）

**现象**：批量从 ScienceDirect 逐篇下载 PDF，开头几篇正常，之后全部失败，页面或接口报
`CPE00002`，同时给出一个 IP 地址。等 8 分钟左右再试又恢复。

**原因**：Elsevier 有按 IP 的下载频率限制。机器节奏（每秒一篇）和人类节奏差别太大，
触发限流后**同一机构 IP 下的所有人**都会受影响——教学网段里其他人也会被连累。

**解法**：

1. **拉开间隔**：每篇之间 5–10 秒，不并发、不批量重试
2. **被限后立即停手**：不要循环重试，那会延长封禁；等 8–10 分钟
3. **先下 OA 那批**（HTTP 直连、不碰出版社），把需要登录的压到最少
4. 触发后**如实告诉用户**：`被 ScienceDirect 限流（CPE00002），等约 8 分钟继续`

批量务必用 `scripts/lit_triage.py --download`（逐篇串行，**默认每篇间隔 5 秒**，
可用 `--delay` 调整，低于 0.4 会按 0.4 算），不要自己写 for 循环。

**别做的事**：换 User-Agent、换代理池、并发重试——那是绕过限流，属于违约行为，
而且只会让机构 IP 被封得更久。
