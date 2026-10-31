# 三分钟上手

## 1. 装入 DSH（推荐）

```bash
python install/install_dsh.py --all
```

然后**完全退出 DSH 再打开**（托盘图标 → 退出，不是关窗口）。
重启后新开会话，让助手列一下工具：应该出现 `mcp__playwright__*`、`mcp__edge__*`、`mcp__browser__*`。

### 额外一步：加载浏览器扩展（只需一次）

打开 `edge://extensions` 或 `chrome://extensions` → 打开「开发人员模式」→
「加载解压缩的扩展」→ 选择 `%USERPROFILE%\.dsh\browser-bridge\extension`。
装好立刻生效，不用重启浏览器。不装也能用，只是 `mcp__browser__*` 读不到东西。

## 2. 确认装好了

```bash
python install/install_dsh.py --check    # 退出码必须是 0
python scripts/smoke_test.py             # 五项全绿
```

`--check` 里必须看到「使用手册 (skill) : 存在」—— 这一行决定助手遇到付费墙时
会不会停下来找你，而不是自己乱撞。缺了就重跑 `install_dsh.py --all`。

## 3. 开始用

```
你：帮我查一下 <某主题> 的文献
助手：→ 先用开放 API 检索，能免费拿的直接下载
     → 剩下需要订阅的，列个清单问你
     → 你说哪几篇，再开浏览器让你登录
```

常用命令：

```bash
python scripts/lit_search.py --author "Jane Smith"      # 检索 + 判断是否免费
python scripts/lit_fetch.py --doi 10.xxxx/yyyy          # 单篇下载（仅开放获取）
python scripts/lit_triage.py --dois-file dois.txt       # 一批 DOI 分流
python scripts/parse_export.py --latest                 # 解析你导出的文件
```

## 注意

- **只下载开放获取**。闭源文献不会自动抓取——这是法律边界，不是技术限制。
- 需要登录时，助手会把窗口打开，**登录由你来做**。
- 用完记得关掉弹出的浏览器窗口。

完整说明见 `README.md`，合规边界见 `SKILL.md` 的「绝对不能做的事」，踩过的坑见 `docs/gotchas.md`。
