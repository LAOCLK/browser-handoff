# Windows 验证清单

这份清单用于在 Windows 上验证插件是否可用。**照顺序做，每步记录结果**。

## 前置检查

```powershell
python --version          # 需要 3.9 或更高
where python
```

如果 `python` 不可用，试试 `py --version`，后面所有命令把 `python` 换成 `py`。

---

## 第 1 步：试运行（不改任何东西）

```powershell
python install\install_dsh.py --dry-run --all
```

**期望输出**里有这几行：

```
dsh CLI  : C:\Users\<你>\AppData\Local\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd
node     : C:\Users\<你>\.dsh\dsh-runtimes\...\node\bin\node.exe
client   : @deepseek-ai/dsh-mcp-client@<与你的 DSH 同版本>
```

**重点看 `dsh CLI` 和 `node` 两行是不是「（找不到）」**。

- 两个都找到了 → 进第 2 步
- 任何一个找不到 → **停**，把完整输出发回来（这是 Windows 路径探测的问题，需要修）

## 第 2 步：正式安装

```powershell
python install\install_dsh.py --all
```

`--all` = 主通道 + Edge 通道 + 浏览器桥。

装完会打印浏览器桥的提示，其中包含**扩展目录路径**，形如：

```
  已安装桥服务: C:\Users\<你>\.dsh\browser-bridge\browser_bridge_win.py
  已安装浏览器扩展: C:\Users\<你>\.dsh\browser-bridge\extension
  ⚠ 还需要在浏览器里加载一次扩展（只需一次，之后自动跟随）
```

### 第 2.5 步：加载扩展（只需一次）

1. 打开 `edge://extensions`（Chrome 用 `chrome://extensions`）
2. 打开左下角「开发人员模式」
3. 点「加载解压缩的扩展」，选择 `C:\Users\<你>\.dsh\browser-bridge\extension`

装好立刻生效，不用重启浏览器，也不用重启 DSH。

## 第 3 步：重启并确认工具

完全退出 DSH（任务栏图标右键 → 退出，不是关窗口），重新打开，新开会话，让助手：

> 列出你当前所有的浏览器工具，按前缀分组统计数量

**期望**：

| 前缀 | 数量 |
|---|---|
| `mcp__playwright__*` | 约 25 |
| `mcp__edge__*` | 约 25 |
| `mcp__browser__*` | **4**（list_tabs / current_tab / get_text / open_url） |

## 第 4 步：冒烟测试

```powershell
python scripts\smoke_test.py
```

**期望**：

```
  ✅ Python 编译      N 个文件
  ✅ 配置块生成       3 份配置块齐全
  ✅ 桥协议           桥协议全部通过
  ✅ chrome 自检      总体： 通过
  ✅ 本机安装状态      ...
结果： 全部通过
```

> 「桥协议」这一项不启动浏览器，只用假扩展验证 MCP 契约，任何机器都该通过。
> 如果它失败，说明桥服务或扩展协议被改坏了，与浏览器无关。

## 第 5 步：真实功能（不需要登录）

```powershell
python scripts\lit_search.py --doi 10.1371/journal.pone.0000308
python scripts\lit_fetch.py  --doi 10.1371/journal.pone.0000308
python scripts\lit_fetch.py  --doi 10.1126/science.aaq0216
```

**期望**：

1. 第一条：显示 `[OA]`，有期刊和被引数
2. 第二条：`✅ 已下载 PDF：...` ，文件在 `%USERPROFILE%\Downloads\papers\`
3. 第三条：打印【需要你操作】段落，**退出码 2**

退出码这样看：

```powershell
echo $LASTEXITCODE
```

## 第 6 步：浏览器能否真的起来

让助手：

> 用 Edge 打开 https://example.com，告诉我页面标题

**期望**：真的弹出一个 Edge 窗口，标题是 `Example Domain`。

## 第 7 步：浏览器桥（读你正在用的浏览器）

先在 Edge 里随便开两个网页（比如 example.com 和 bing.com），然后让助手：

> 列出我 Edge 里现在开着的标签页

**期望**：返回真实标签页的标题和 URL（就是你刚才开的那几个）。

再让助手：

> 读一下我当前这个标签页的正文

**期望**：返回页面正文纯文本（`example.com` 会返回 `Example Domain` 那段）。

如果返回「没有检测到任何已连接的浏览器扩展」，回到第 2.5 步检查扩展是否装好、
浏览器是否正在运行。详细排查见 `docs/windows-bridge.md`。

---


## 已知的 Windows 坑（实测记录）

### 1. `python` 可能是应用商店的占位符

```
$ python --version
exit=9009
```

那是 Windows 自带的"应用执行别名"，不是真 Python。用 DSH 自带的：

```powershell
$py = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $py install\install_dsh.py --dry-run --all
```

### 2. 安装报 EPERM / package.json.lock

```
EPERM: open 'C:\Users\<你>\.dsh\profiles\desktop\package.json.lock'
```

DSH 还开着，插件管理器拿不到锁。**完全退出 DSH 再装**。
0.4.9 起安装器会自动重试 3 次，仍失败会直接告诉你原因。

### 3. 中文输出乱码

管道/重定向时 Python 默认用 GBK。0.4.9 起脚本会自动切 UTF-8；
如果你的环境还是乱，加一句：

```powershell
$env:PYTHONUTF8 = "1"
```

### 4. 截图保存被拒：outside allowed roots

```
Error: File access denied: D:\... is outside allowed roots.
Allowed roots: C:\Users\<你>\browser-handoff-artifacts, ...
```

Playwright MCP 只允许往 `--output-dir` 里写。**截图和导出文件要放到那个目录**
（配置里是 `%USERPROFILE%\browser-handoff-artifacts`），不要指到工作区。

### 5. 扩展加载了，但工具仍说「没有检测到浏览器扩展」

按顺序排查：

1. 扩展是不是装在了**正在运行的那个**浏览器里（Edge 和 Chrome 要各装一次）
2. `edge://extensions` 里该扩展是否处于「已启用」状态
3. 桥服务是否在跑：`python scripts\bridge_selftest.py` 应全绿（它不依赖浏览器）
4. 端口是否被占：桥用 `8790-8799`，扩展会连所有应答的端口，一般不会冲突；
   但若 firewal 或安全软件拦本地回环，也会连不上

### 6. 加载扩展时报「无法加载扩展程序」

选目录时要选到**含 `manifest.json` 的那一层**：
`C:\Users\<你>\.dsh\browser-bridge\extension`，不是它的上一级 `browser-bridge`。

## 出问题时请回传这些

1. 第 1 步的**完整输出**
2. `python --version` 和 `py --version`（如果都有）
3. DSH 的版本（设置里或 `dsh --version`）
4. 报错的**完整文本**，不要只截一行
5. 安装目录：

```powershell
dir "%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin"
dir "%USERPROFILE%\.dsh\profiles\desktop"
```

---

## 已知不支持 / 未验证

| 项 | 状态 |
|---|---|
| 浏览器桥（读你正在用的浏览器） | ✅ Windows 已支持（0.5.0，扩展方式），见第 7 步 |
| Safari | ❌ Windows 上没有 Safari，`browser` 只接受 `edge` / `chrome` |
| Firefox | ❌ 任何平台都不支持 |
| 桥的点击 / 输入 | ❌ 桥只读（列标签、读正文、开新标签），操作请用 Playwright 通道 |
| 页面抽取 `--extract` | ⚠️ 未在 Windows 上验证 |
| 中文路径 | ⚠️ 未验证（如果用户名含中文，留意报错） |
