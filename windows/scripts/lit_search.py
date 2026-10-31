#!/usr/bin/env python3
"""lit_search.py — 只用开放 API 做文献检索，不爬任何数据库。

数据源（全部是官方开放接口，明确允许程序化访问）：
    OpenAlex   元数据 / 作者 / 引用数 / OA 状态
    Unpaywall  DOI → 合法的开放获取副本（出版社或机构仓库）
    Crossref   DOI 校验 / 期刊与出版信息
    arXiv      预印本检索

不做的事：
    ✗ 抓取 Web of Science / Scopus / 知网 等订阅数据库
    ✗ 抓取 Google Scholar
    ✗ 访问 Sci-Hub 或任何镜像
    ✗ 绕过付费墙

用法：
    python3 lit_search.py --doi 10.1371/journal.pone.0000308
    python3 lit_search.py --title "perfluorinated compounds wastewater Beijing"
    python3 lit_search.py --author "Jane Smith" --limit 10
    python3 lit_search.py --doi ... --json

环境变量（可选，但强烈建议）：
    OPENALEX_MAILTO     进 OpenAlex 礼貌池，配额更高
    UNPAYWALL_EMAIL     Unpaywall 必需；没有就跳过该源
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:  # Windows 上输出被重定向时默认用 GBK，会让中文变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

UA = "lit-search/0.4 (+open-apis-only)"
TIMEOUT = 25


# ---------------------------------------------------------------- HTTP

def get_json(url: str, retries: int = 3) -> dict | None:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:                     # 限流，退避
                time.sleep(2 + attempt * 3)
                continue
            if exc.code == 404:
                return None
            raise
        except Exception as exc:                    # noqa: BLE001
            last = exc
            time.sleep(1)
    if last:
        print("  [warn] %s" % last, file=sys.stderr)
    return None


def q(s: str) -> str:
    return urllib.parse.quote(s)


# ---------------------------------------------------------------- 数据源

def openalex_doi(doi: str) -> dict | None:
    url = "https://api.openalex.org/works/https://doi.org/" + doi
    mailto = os.environ.get("OPENALEX_MAILTO")
    if mailto:
        url += "?mailto=" + q(mailto)
    return get_json(url)


def openalex_search(title: str, limit: int) -> list[dict]:
    url = "https://api.openalex.org/works?search=%s&per_page=%d" % (q(title), limit)
    mailto = os.environ.get("OPENALEX_MAILTO")
    if mailto:
        url += "&mailto=" + q(mailto)
    return (get_json(url) or {}).get("results", [])


def openalex_author(name: str) -> list[dict]:
    data = get_json("https://api.openalex.org/authors?search=%s&per_page=5" % q(name))
    return (data or {}).get("results", [])


def unpaywall(doi: str) -> dict | None:
    """返回最合法的 OA 副本位置；没有邮箱就跳过（该服务要求署名）。"""
    email = os.environ.get("UNPAYWALL_EMAIL") or os.environ.get("OPENALEX_MAILTO")
    if not email:
        return None
    return get_json("https://api.unpaywall.org/v2/%s?email=%s" % (q(doi), q(email)))


def crossref(doi: str) -> dict | None:
    data = get_json("https://api.crossref.org/works/" + q(doi))
    return (data or {}).get("message")


# ---------------------------------------------------------------- 整理与展示

def normalize(w: dict) -> dict:
    loc = w.get("best_oa_location") or {}
    oa = w.get("open_access") or {}
    src = ((w.get("primary_location") or {}).get("source") or {})
    return {
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "venue": src.get("display_name"),
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "cited": w.get("cited_by_count"),
        "is_oa": bool(oa.get("is_oa")),
        "oa_url": oa.get("oa_url") or loc.get("pdf_url") or loc.get("landing_page_url"),
        "oa_status": oa.get("oa_status"),
    }


def show(items: list[dict], show_closed: bool = True) -> None:
    if not items:
        print("没有结果。")
        return
    for i, w in enumerate(items, 1):
        flag = "OA    " if w["is_oa"] else "CLOSED"
        if not w["is_oa"] and not show_closed:
            continue
        print("%2d. [%s] %s (%s)" % (i, flag, w["title"][:100], w["year"] or "?"))
        print("     期刊: %s | 被引: %s" % (w["venue"] or "-", w["cited"]))
        if w["doi"]:
            print("     DOI : %s   https://doi.org/%s" % (w["doi"], w["doi"]))
        if w["oa_url"]:
            print("     OA  : %s" % w["oa_url"])
        print()


def enrich_with_unpaywall(item: dict) -> list[str]:
    """返回该 DOI 的合法 OA 链接列表（出版社 / 机构仓库）。"""
    info = unpaywall(item["doi"])
    if not info:
        return []
    links = []
    for loc in info.get("oa_locations") or []:
        url = loc.get("url_for_pdf") or loc.get("url")
        if url and url not in links:
            links.append(url)
    return links[:3]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="开放文献检索（OpenAlex / Crossref / Unpaywall / arXiv）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--doi")
    g.add_argument("--title")
    g.add_argument("--author")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--oa-only", action="store_true", help="只显示有免费全文的")
    args = ap.parse_args()

    result = {"query": {}, "items": []}

    if args.doi:
        result["query"] = {"doi": args.doi}
        w = openalex_doi(args.doi)
        if not w:
            print("OpenAlex 没有这个 DOI。", file=sys.stderr)
            return 1
        item = normalize(w)
        links = enrich_with_unpaywall(item)
        if links:
            item["oa_url"] = links[0]
            item["is_oa"] = True
            item["extra_oa_links"] = links
        cr = crossref(args.doi)
        if cr:
            item["publisher"] = (cr.get("publisher") or "")
        result["items"] = [item]

    elif args.title:
        result["query"] = {"title": args.title}
        result["items"] = [normalize(w) for w in openalex_search(args.title, args.limit)]

    else:
        result["query"] = {"author": args.author}
        authors = openalex_author(args.author)
        if not authors:
            print("没找到该作者。", file=sys.stderr)
            return 1
        result["candidates"] = [
            {"id": a["id"].split("/")[-1], "name": a["display_name"],
             "works": a["works_count"], "h": a["summary_stats"]["h_index"],
             "institutions": [i["display_name"] for i in
                              (a.get("last_known_institutions") or [])[:2]],
             "orcid": a.get("orcid")}
            for a in authors
        ]
        aid = authors[0]["id"].split("/")[-1]
        url = ("https://api.openalex.org/works?filter=author.id:%s"
               "&sort=cited_by_count:desc&per_page=%d" % (aid, args.limit))
        result["items"] = [normalize(w) for w in (get_json(url) or {}).get("results", [])]

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if result.get("candidates"):
        print("同名作者候选（请让用户确认是哪一位）：")
        for c in result["candidates"][:5]:
            print("  %s | %s 篇 | h=%-3s | %s %s" % (
                c["name"], c["works"], c["h"],
                "; ".join(c["institutions"]) or "-",
                ("| " + c["orcid"]) if c["orcid"] else ""))
        print()

    show(result["items"], show_closed=not args.oa_only)

    closed = [w for w in result["items"] if not w["is_oa"]]
    if closed:
        print("→ %d 篇没有找到合法免费副本。" % len(closed))
        print("   可以用浏览器打开 DOI 页面，由用户用自己的权限访问；")
        print("   不要去找镜像站——那是侵权。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
