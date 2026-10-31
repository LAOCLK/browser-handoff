# 三分钟上手

## 1. 装入 DSH（推荐）

```bash
python3 install/install_dsh.py --all
```

然后**完全退出 DSH 再打开**（⌘Q，不是关窗口）。
重启后新开会话，让助手列一下工具：应该出现 `mcp__playwright__*`、`mcp__edge__*`、`mcp__browser__*`。

## 2. 确认装好了

```bash
python3 scripts/smoke_test.py
```

四项全绿即可用（编译 / 配置块 / 浏览器自检 / 本机安装状态）。

## 3. 开始用

```
你：帮我查一下 <某主题> 的文献
助手：→ 先用开放 API 检索，能免费拿的直接下载
     → 剩下需要订阅的，列个清单问你
     → 你说哪几篇，再开浏览器让你登录
```

常用命令：

```bash
python3 scripts/lit_search.py --author "Jane Smith"      # 检索 + 判断是否免费
python3 scripts/lit_fetch.py --doi 10.xxxx/yyyy          # 单篇下载（仅开放获取）
python3 scripts/lit_triage.py --dois-file dois.txt       # 一批 DOI 分流
python3 scripts/parse_export.py --latest                 # 解析你导出的文件
```

## 注意

- **只下载开放获取**。闭源文献不会自动抓取——这是法律边界，不是技术限制。
- 需要登录时，助手会把窗口打开，**登录由你来做**。
- 用完记得关掉弹出的浏览器窗口。

完整说明见 `README.md`，合规边界见 `SKILL.md` 的「绝对不能做的事」，踩过的坑见 `docs/gotchas.md`。
