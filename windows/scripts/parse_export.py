#!/usr/bin/env python3
"""parse_export.py — 解析你从数据库导出的文献文件，输出统一表格。

支持：RIS (.ris)、BibTeX (.bib)、WoS 制表符文本 (.txt/.tsv)

用法：
    python3 parse_export.py savedrecs.ris
    python3 parse_export.py --latest          # 自动找 ~/Downloads 里最新的导出文件
    python3 parse_export.py savedrecs.ris --json
    python3 parse_export.py savedrecs.ris --csv out.csv

为什么要有这个：闭源数据库不允许我自动化访问，但**你自己导出**完全合法。
你点导出，我负责把文件变成干净的结构化数据——去重、排序、按年份分组。
"""

from __future__ import annotations
import argparse
import csv
import json
import os
import re
import sys

try:  # Windows 上输出被重定向时默认用 GBK，会让中文变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


# ---------------------------------------------------------------- RIS

def parse_ris(text: str) -> list[dict]:
    records, cur, last_tag = [], {}, None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  -(?: (.*))?$", line)
        if m:
            tag, val = m.group(1), (m.group(2) or "").strip()
            last_tag = tag
            if tag == "TY":
                cur = {"type": val}
                continue
            if not cur:
                cur = {}
            if tag in ("AU", "A1"):
                cur.setdefault("authors", []).append(val)
            elif tag == "TI":
                cur["title"] = val
            elif tag in ("T1",):
                cur.setdefault("title", val)
            elif tag in ("JO", "T2", "JA"):
                cur.setdefault("venue", val)
            elif tag == "PY":
                cur["year"] = val
            elif tag in ("DO",):
                cur["doi"] = val
            elif tag in ("AB",):
                cur["abstract"] = (cur.get("abstract", "") + " " + val).strip()
            elif tag == "ER":
                records.append(cur)
                cur, last_tag = {}, None
            continue
        if line.startswith("      ") and last_tag and cur:
            # 续行
            key = {"AB": "abstract", "TI": "title"}.get(last_tag)
            if key:
                cur[key] = (cur.get(key, "") + " " + line.strip()).strip()
    return [r for r in records if r.get("title")]


# ---------------------------------------------------------------- BibTeX

def parse_bib(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"@(\w+)\s*\{[^,]*,(.*?)\n\}", text, re.S):
        body = m.group(2)
        fields = dict(re.findall(r"(\w+)\s*=\s*[{\"](.+?)[}\"],?\s*$", body, re.M | re.S))
        authors = [a.strip() for a in re.split(r"\s+and\s+", fields.get("author", "")) if a.strip()]
        out.append({
            "type": m.group(1).lower(),
            "title": re.sub(r"\s+", " ", fields.get("title", "")).strip("{} "),
            "authors": authors,
            "venue": fields.get("journal") or fields.get("booktitle"),
            "year": fields.get("year"),
            "doi": fields.get("doi"),
        })
    return [r for r in out if r.get("title")]


# ---------------------------------------------------------------- WoS TSV

def parse_tsv(text: str) -> list[dict]:
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("\t")]
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        def col(name):
            i = idx.get(name)
            return cols[i].strip() if i is not None and i < len(cols) else ""
        out.append({
            "title": col("Article Title") or col("TI"),
            "authors": [a.strip() for a in col("Authors").split(";") if a.strip()],
            "venue": col("Source Title") or col("SO"),
            "year": (col("Publication Year") or col("PY"))[:4],
            "doi": col("DOI"),
            "cited": col("Times Cited, All Databases") or col("TC"),
        })
    return [r for r in out if r.get("title")]


# ---------------------------------------------------------------- 通用

PARSERS = [(".ris", parse_ris), (".bib", parse_bib), (".txt", parse_tsv),
           (".tsv", parse_tsv), (".bibtex", parse_bib)]


def detect(path: str):
    low = path.lower()
    for ext, fn in PARSERS:
        if low.endswith(ext):
            return fn
    return None


def latest_export() -> str | None:
    dl = os.path.expanduser("~/Downloads")
    if not os.path.isdir(dl):
        return None
    cands = []
    for name in os.listdir(dl):
        if detect(name):
            p = os.path.join(dl, name)
            cands.append((os.path.getmtime(p), p))
    return max(cands)[1] if cands else None


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = (it.get("doi") or "").lower() or re.sub(r"\W+", "", (it.get("title") or "").lower())[:80]
        if key and key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="解析数据库导出的文献文件")
    ap.add_argument("path", nargs="?")
    ap.add_argument("--latest", action="store_true", help="自动取 ~/Downloads 里最新的导出文件")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--csv", metavar="OUT")
    args = ap.parse_args()

    path = args.path
    if args.latest and not path:
        path = latest_export()
        if not path:
            print("~/Downloads 里没找到 .ris/.bib/.txt 导出文件。", file=sys.stderr)
            return 1
        print("使用最新文件：%s\n" % path)
    if not path:
        ap.error("给一个文件路径，或用 --latest")

    fn = detect(path)
    if not fn:
        print("不认识的扩展名：%s（支持 .ris/.bib/.txt）" % path, file=sys.stderr)
        return 1

    raw = open(path, encoding="utf-8", errors="ignore").read()
    items = dedupe(fn(raw))
    if not items:
        print("没有解析出记录。文件格式可能不同。", file=sys.stderr)
        return 1

    items.sort(key=lambda x: (-(int(x.get("cited") or 0)),
                              -(int((x.get("year") or "0")[:4] or 0))))

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
    elif args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["title", "authors", "venue", "year", "doi", "cited"])
            for it in items:
                w.writerow([it.get("title", ""), "; ".join(it.get("authors") or []),
                            it.get("venue", ""), it.get("year", ""),
                            it.get("doi", ""), it.get("cited", "")])
        print("已写入 %s（%d 条）" % (args.csv, len(items)))
    else:
        for i, it in enumerate(items, 1):
            print("%2d. %s (%s)" % (i, (it.get("title") or "")[:96], it.get("year") or "?"))
            print("    %s" % (it.get("venue") or "-"))
            if it.get("authors"):
                print("    %s" % "; ".join(it["authors"][:3]) +
                      (" 等 %d 人" % len(it["authors"]) if len(it["authors"]) > 3 else ""))
            if it.get("doi"):
                print("    https://doi.org/%s" % it["doi"])
            print()
        print("共 %d 条（已按被引、年份排序，并按 DOI/标题去重）" % len(items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
