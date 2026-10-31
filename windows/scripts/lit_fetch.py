#!/usr/bin/env python3
"""lit_fetch.py — 自动下载**开放获取**文献（全自动、完全合法）。

只从这些地方拿文件：
    Unpaywall 给出的 OA 副本（出版社 OA / 机构仓库 / 预印本）
    OpenAlex 的 best_oa_location
    页面里的 <meta name="citation_pdf_url">

拿不到就明确说"没有合法免费副本"，绝不回退到镜像站。

用法：
    python3 lit_fetch.py --doi 10.1371/journal.pone.0000308
    python3 lit_fetch.py --doi 10.1126/science.aaq0216      # 会拒绝（闭源）
    python3 lit_fetch.py --url https://arxiv.org/abs/2101.00001
    python3 lit_fetch.py --doi ... --format html --out ~/papers

格式策略（可用 --format 覆盖）：
    pdf（默认）  只要 PDF；只有 HTML 时尝试从页面里找 PDF 链接
    html         接受 HTML 落页
    auto         有什么存什么

退出码：0 成功，2 没有合法免费副本，3 需要用户决定（可用 --yes 跳过询问）
"""

from __future__ import annotations
import argparse
import json
import os
import re
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

UA = "lit-fetch/0.4 (open-access only)"
TIMEOUT = 40



CONFIG = os.path.expanduser("~/.dsh/lit-fetch.json")


def load_cfg() -> dict:
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_cfg(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def ask_with_timeout(prompt: str, seconds: int = 10) -> str:
    """终端里问一句；超时或非交互直接返回空串。"""
    if not sys.stdin.isatty():
        return ""
    import select
    sys.stdout.write(prompt)
    sys.stdout.flush()
    ready, _, _ = select.select([sys.stdin], [], [], seconds)
    if not ready:
        sys.stdout.write("\n")
        return ""
    return sys.stdin.readline().strip().lower()


def resolve_format(cli_value: str | None, yes: bool) -> str:
    """优先级：命令行 > 配置文件 > 交互询问（10 秒）> pdf。"""
    if cli_value:
        return cli_value
    cfg = load_cfg()
    if cfg.get("format") in ("pdf", "html", "auto"):
        return cfg["format"]
    if not yes:
        ans = ask_with_timeout("保存格式？[pdf/html/auto]（10 秒不回答 = pdf）: ")
        if ans in ("pdf", "html", "auto"):
            cfg["format"] = ans
            save_cfg(cfg)
            return ans
    return "pdf"


def get(url: str, retries: int = 3) -> tuple[bytes, str]:
    """返回 (内容, content-type)。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read(), (r.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 503):
                time.sleep(2 + attempt * 3)
                continue
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1)
    raise last  # type: ignore[misc]


def json_api(url: str) -> dict | None:
    try:
        raw, _ = get(url)
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def oa_candidates(doi: str) -> tuple[list[str], dict]:
    """收集合法 OA 链接，PDF 优先。"""
    links: list[str] = []
    meta: dict = {}

    email = os.environ.get("UNPAYWALL_EMAIL") or os.environ.get("OPENALEX_MAILTO")
    if email:
        up = json_api("https://api.unpaywall.org/v2/%s?email=%s"
                      % (urllib.parse.quote(doi), urllib.parse.quote(email)))
        if up:
            meta["is_oa"] = up.get("is_oa")
            for loc in up.get("oa_locations") or []:
                for key in ("url_for_pdf", "url"):
                    u = loc.get(key)
                    if u and u not in links:
                        links.append(u)
            links.sort(key=lambda u: 0 if "pdf" in u.lower() else 1)

    if not links:
        w = json_api("https://api.openalex.org/works/https://doi.org/"
                     + urllib.parse.quote(doi))
        if w:
            meta["is_oa"] = (w.get("open_access") or {}).get("is_oa")
            meta["title"] = w.get("title")
            meta["year"] = w.get("publication_year")
            auths = w.get("authorships") or []
            if auths:
                meta["author"] = ((auths[0].get("author") or {})
                                  .get("display_name") or "")
            loc = w.get("best_oa_location") or {}
            for key in ("pdf_url", "landing_page_url"):
                u = loc.get(key)
                if u:
                    links.append(u)
    return links, meta


def find_pdf_in_html(html: bytes) -> str | None:
    text = html.decode("utf-8", "ignore")
    m = re.search(r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
                  text, re.I)
    if m:
        return m.group(1)
    m = re.search(r'href=["\']([^"\']+\.pdf)["\']', text, re.I)
    return m.group(1) if m else None


def slug(s: str, limit: int = 55) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff -]", "", s or "").strip()
    s = re.sub(r"\s+", "-", s)
    if len(s) > limit:                      # 在词边界截断，别切半个单词
        s = s[:limit].rsplit("-", 1)[0]
    return (s or "paper").strip("-")


def filename_for(meta: dict, ext: str) -> str:
    year = meta.get("year") or "n.d."
    author = ((meta.get("author") or "").split() or ["anon"])[-1]
    title = slug(meta.get("title") or "")
    return "%s-%s-%s.%s" % (year, slug(author, 20), title, ext)


def save(data: bytes, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path



def fetch_one(links: list[str], meta: dict, out_dir: str,
              fmt: str = "pdf") -> tuple[str | None, str]:
    """把第一个能拿到的文件存下来，返回路径；拿不到返回 None。

    逻辑与命令行一致：PDF 优先；落页里有 citation_pdf_url 就跟进；
    fmt=html/auto 时接受网页。
    """
    last = ""
    for url in links[:4]:
        try:
            data, ctype = get(url)
        except Exception as exc:  # noqa: BLE001
            last = "请求失败：%s" % exc
            continue

        if data[:5] == b"%PDF-" or "application/pdf" in ctype:
            return save(data, os.path.join(out_dir, filename_for(meta, "pdf"))), "ok"

        if fmt in ("html", "auto"):
            return save(data, os.path.join(out_dir, filename_for(meta, "html"))), "html"

        pdf = find_pdf_in_html(data)
        if pdf:
            if pdf.startswith("/"):
                pdf = urllib.parse.urljoin(url, pdf)
            try:
                pdata, _ = get(pdf)
                if pdata[:5] == b"%PDF-":
                    return (save(pdata, os.path.join(out_dir, filename_for(meta, "pdf"))),
                            "ok")
            except Exception as exc:  # noqa: BLE001
                last = "取 PDF 失败：%s" % exc
    return None, last or "没有可用的 PDF 链接"


def main() -> int:
    ap = argparse.ArgumentParser(description="下载开放获取文献（仅合法来源）")
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--doi")
    g.add_argument("--url")
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"),
                                                 "Downloads", "papers"))
    ap.add_argument("--format", choices=["pdf", "html", "auto"], default=None,
                    help="不指定就先问一次；10 秒不回答按 pdf")
    ap.add_argument("--set-format", choices=["pdf", "html", "auto"],
                    help="把这个格式记成默认值后退出")
    ap.add_argument("--yes", action="store_true", help="需要用户决定时默认继续")
    args = ap.parse_args()

    if args.set_format:
        cfg = load_cfg()
        cfg["format"] = args.set_format
        save_cfg(cfg)
        print("已记住默认格式：%s（保存在 %s）" % (args.set_format, CONFIG))
        return 0

    if not (args.doi or args.url):
        ap.error("需要 --doi 或 --url（只想设默认格式就用 --set-format）")

    fmt = resolve_format(args.format, args.yes)

    if args.url:
        links, meta = [args.url], {}
    else:
        links, meta = oa_candidates(args.doi)
        if not links:
            # 这一段是给人看的指引，走 stdout——PowerShell 会把 stderr 渲染成红色错误
            print("没有找到合法免费副本（OpenAlex/Unpaywall 都没有）。")
            print("")
            print("【需要你操作】这篇得用你自己的权限拿：")
            print("  1. 浏览器打开 https://doi.org/%s" % args.doi)
            print("  2. 用机构账号登录后下载这一篇")
            print("  或者直接跟我说「打开这篇」，我开窗口给你登录。")
            return 2

    last_err = None
    for url in links[:4]:
        try:
            data, ctype = get(url)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue

        is_pdf = data[:5] == b"%PDF-" or "application/pdf" in ctype
        if is_pdf:
            name = filename_for(meta, "pdf")
            path = save(data, os.path.join(args.out, name))
            print("✅ 已下载 PDF：%s（%.1f MB）" % (path, len(data) / 1e6))
            return 0

        if fmt == "html" or fmt == "auto":
            name = filename_for(meta, "html")
            path = save(data, os.path.join(args.out, name))
            print("✅ 已保存 HTML：%s（%d KB）" % (path, len(data) // 1024))
            return 0

        # 落到 HTML 落页：试着找 PDF
        pdf = find_pdf_in_html(data)
        if pdf:
            if pdf.startswith("/"):
                pdf = urllib.parse.urljoin(url, pdf)
            try:
                pdata, ptype = get(pdf)
                if pdata[:5] == b"%PDF-" or "application/pdf" in ptype:
                    name = filename_for(meta, "pdf")
                    path = save(pdata, os.path.join(args.out, name))
                    print("✅ 已下载 PDF：%s（%.1f MB）" % (path, len(pdata) / 1e6))
                    return 0
            except Exception as exc:  # noqa: BLE001
                last_err = exc

        print("⚠️  这个地址只有网页版，没有 PDF（当前偏好：%s），未保存：%s" % (fmt, url))
        if fmt == "pdf":
            print("   需要网页版就加 --format html，或改默认：--set-format html")
            return 3

    print("未能取得文件。最后一个错误：%s" % last_err, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
