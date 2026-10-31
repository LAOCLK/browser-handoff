#!/usr/bin/env python3
"""lit_triage.py — 把一批 DOI 分成「可免费下载」和「需要登录」两堆。

这实现了约定的顺序：
    1. 免费的用 HTTP 直接拿完（不碰浏览器）
    2. 付费的只列清单，等用户点名，再一次一篇走浏览器

用法：
    python3 lit_triage.py --dois-file dois.txt
    python3 lit_triage.py --from-export ~/Downloads/savedrecs.ris
    python3 lit_triage.py --dois-file dois.txt --download --out ~/papers

产物：
    <out>/oa-*.pdf          选 --download 时下载的免费全文
    <out>/needs-login.txt   需要登录的清单（DOI + 标题 + 链接）

合法边界（写死）：
    - 只对开放获取发 HTTP 请求；拿不到就归入 needs-login，不尝试绕过
    - 不并发、不循环猜镜像；每条间隔 0.4 秒
"""

from __future__ import annotations
import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lit_fetch  # noqa: E402  同目录工具复用

try:  # Windows 上输出被重定向时默认用 GBK，会让中文变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def read_dois_file(path: str) -> list[str]:
    dois = []
    for line in open(path, encoding="utf-8", errors="ignore"):
        for m in re.finditer(r"10\.\d{4,9}/[^\s\"'<>,;]+", line):
            dois.append(m.group(0).rstrip(".").lower())
    return list(dict.fromkeys(dois))


def read_dois_ris(path: str) -> list[str]:
    text = open(path, encoding="utf-8", errors="ignore").read()
    return list(dict.fromkeys(d.lower()
                              for d in re.findall(r"^DO  - (.+)$", text, re.M)))


def main() -> int:
    ap = argparse.ArgumentParser(description="把 DOI 分成 OA / 需登录 两堆")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dois-file")
    g.add_argument("--from-export")
    ap.add_argument("--download", action="store_true", help="顺手下载 OA 的那些")
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads/papers"))
    args = ap.parse_args()

    dois = (read_dois_ris(args.from_export) if args.from_export
            else read_dois_file(args.dois_file))
    if not dois:
        print("没读到 DOI。", file=sys.stderr)
        return 1

    print("共 %d 个 DOI，开始分流（每条间隔 0.4 秒）...\n" % len(dois))
    oa, closed = [], []

    for i, doi in enumerate(dois, 1):
        try:
            links, meta = lit_fetch.oa_candidates(doi)
        except Exception as exc:  # noqa: BLE001
            print("  [%d/%d] %s → 查询失败：%s" % (i, len(dois), doi, exc))
            closed.append({"doi": doi, "title": "", "reason": "查询失败"})
            continue

        if links:
            oa.append({"doi": doi, "url": links[0],
                       "title": meta.get("title") or "",
                       "year": meta.get("year"), "author": meta.get("author")})
            print("  [%d/%d] OA      %s" % (i, len(dois), doi))
        else:
            closed.append({"doi": doi, "title": meta.get("title") or "",
                           "reason": "无免费副本"})
            print("  [%d/%d] CLOSED  %s" % (i, len(dois), doi))
        time.sleep(0.4)

    os.makedirs(args.out, exist_ok=True)

    if args.download and oa:
        print("\n下载 %d 篇开放获取..." % len(oa))
        for item in oa:
            try:
                path, why = lit_fetch.fetch_one([item["url"]], item, args.out)
                if path:
                    print("  ✅ %s" % path)
                else:
                    print("  ⚠️  %s → %s" % (item["doi"], why))
            except Exception as exc:  # noqa: BLE001
                print("  ❌ %s：%s" % (item["doi"], exc))
            time.sleep(0.4)

    if closed:
        listing = os.path.join(args.out, "needs-login.txt")
        with open(listing, "w", encoding="utf-8") as f:
            for c in closed:
                f.write("%s\t%s\t%s\n" % (c["doi"], c["title"][:120], c["reason"]))
        print("\n需要登录的 %d 篇已写入：%s" % (len(closed), listing))
        print("")
        print("【需要你决定】这些没有免费副本，只能走机构权限：")
        for c in closed[:5]:
            print("  - %s  %s" % (c["doi"], c["title"][:70]))
        if len(closed) > 5:
            print("  ... 还有 %d 篇，见文件" % (len(closed) - 5))
        print("  告诉我哪几篇要拿（1–3 篇我逐篇帮你开窗登录）。")

    print("\n小结：OA %d 篇 / 需登录 %d 篇 / 共 %d 篇"
          % (len(oa), len(closed), len(dois)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
