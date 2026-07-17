#!/usr/bin/env python3
"""ICLR / OpenReview 论文搜索工具

搜索 ICLR 会议论文（及其他 OpenReview 托管会议），支持按关键词、
主题、作者搜索，可以过滤录取状态（Oral/Poster/全部）。

用法:
  python3 iclr_openreview_search.py "multi-granularity memory" --venue "ICLR 2026" --limit 10
  python3 iclr_openreview_search.py "transformer" --venue "ICLR 2026" --status poster
  python3 iclr_openreview_search.py "attention" --all-venues --limit 20

依赖: requests, 无需 API key
"""
import json, sys, time, argparse
from urllib.parse import quote_plus

import requests


BASE_URL = "https://api.openreview.net"
BASE_URL_V2 = "https://api2.openreview.net"

# ICLR 各届已知 venue 值（OpenReview content.venue 字段）
KNOWN_VENUES = {
    "ICLR 2026": {
        "accepted": ["ICLR 2026 Poster", "ICLR 2026 Oral"],
        "submitted": ["Submitted to ICLR 2026"],
    },
    "ICLR 2025": {
        "accepted": ["ICLR 2025 Poster", "ICLR 2025 Oral"],
        "submitted": ["Submitted to ICLR 2025"],
    },
    "ICLR 2024": {
        "accepted": ["ICLR 2024 Poster", "ICLR 2024 Oral", "ICLR 2024 Spotlight"],
        "submitted": ["Submitted to ICLR 2024"],
    },
    "ICLR 2023": {
        "accepted": ["ICLR 2023 Poster", "ICLR 2023 Oral", "ICLR 2023 Spotlight"],
        "submitted": ["Submitted to ICLR 2023"],
    },
    "ICLR 2022": {
        "accepted": ["ICLR 2022 Poster", "ICLR 2022 Oral", "ICLR 2022 Spotlight"],
        "submitted": ["Submitted to ICLR 2022"],
    },
}


def parse_v2_note(note):
    """将 v2 API 的嵌套 {'value': ...} 格式展平为平铺 dict"""
    flat = {}
    for k, v in note.get("content", {}).items():
        if isinstance(v, dict) and "value" in v:
            flat[k] = v["value"]
        elif isinstance(v, list):
            flat[k] = v
        else:
            flat[k] = v
    flat["forum"] = note.get("forum", "")
    return flat


def fetch_submission_note(forum_id):
    """按 forum ID 获取论文原文（submission note）

    v2 search 可能返回 review/rebuttal note，它们缺少完整元数据。
    需要通过 forum ID 拿到 submission note 才能获取标题/作者等信息。
    返回完整的 note dict（content 已展平）。
    """
    try:
        r = requests.get(
            f"{BASE_URL_V2}/notes",
            params={"id": forum_id},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for n in data.get("notes", []):
            c = n.get("content", {})
            title_val = c.get("title", {})
            if isinstance(title_val, dict):
                raw = title_val.get("value", "")
                if raw and raw.strip():
                    # 展平 content 并返回完整 note
                    n["content"] = parse_v2_note(n)
                    return n
        return None
    except Exception:
        return None


def search_openreview(term, venue_values, limit=10, retries=3):
    """通过 OpenReview v2 API 搜索论文（两阶段：搜→查）

    阶段1：通过 /notes/search 找到匹配的 forums（可能匹配到 review/rebuttal）
    阶段2：通过按 forum ID 查 /notes 获取 submission note（原文信息）
    """
    found_forums = []
    seen_forums = set()

    for venue_val in venue_values:
        params = {
            "content.venue": venue_val,
            "term": term,
            "limit": min(limit * 5, 200),
        }
        for attempt in range(retries):
            try:
                r = requests.get(
                    f"{BASE_URL_V2}/notes/search",
                    params=params,
                    timeout=30,
                )
                if r.status_code == 429:
                    time.sleep(2 ** attempt + 1)
                    continue
                r.raise_for_status()
                data = r.json()
                for n in data.get("notes", []):
                    forum = n.get("forum", "")
                    if forum and forum not in seen_forums:
                        seen_forums.add(forum)
                        found_forums.append(forum)
                break
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    print(f"  ⚠️ 检索 {venue_val} 失败: {e}", file=sys.stderr)

        if len(found_forums) >= limit:
            break

    # 如果 v2 找到的不够，fallback v1
    if len(found_forums) < limit:
        for venue_val in venue_values:
            try:
                r = requests.get(
                    f"{BASE_URL}/notes/search",
                    params={"content.venue": venue_val, "term": term, "limit": limit * 5},
                    timeout=30,
                )
                r.raise_for_status()
                data = r.json()
                for n in data.get("notes", []):
                    forum = n.get("forum", "")
                    if forum and forum not in seen_forums:
                        seen_forums.add(forum)
                        found_forums.append(forum)
                    if len(found_forums) >= limit:
                        break
            except Exception:
                pass
            if len(found_forums) >= limit:
                break

    # 阶段2：按 forum ID 获取 submission notes
    all_notes = []
    for fid in found_forums[:limit]:
        note = fetch_submission_note(fid)
        if note:
            all_notes.append(note)

    return all_notes[:limit]


def format_paper(note):
    """格式化单篇论文输出"""
    c = note.get("content", {})
    title = c.get("title", "")
    venue = c.get("venue", "?")
    abstract = c.get("abstract", "")
    forum = note.get("forum", "")
    invitation = note.get("invitation", "")

    # 提取作者
    authors = c.get("authors", [])
    author_str = ", ".join(authors[:5])
    if len(authors) > 5:
        author_str += " et al."

    # OpenReview 链接
    or_link = f"https://openreview.net/forum?id={forum}" if forum else ""

    # arXiv ID（如果论文有 arXiv 版本）
    arxiv_id = c.get("arxiv_id", "")
    arxiv_link = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""

    # PDF link
    pdf_link = f"https://openreview.net/pdf?id={forum}" if forum else ""

    lines = [
        f"📄 **{title}**",
        f"   🏷 {venue}",
        f"   👥 {author_str}",
    ]
    if abstract:
        abs_short = abstract[:300].replace("\n", " ")
        lines.append(f"   📝 {abs_short}{'...' if len(abstract) > 300 else ''}")
    if or_link:
        lines.append(f"   🔗 OpenReview: {or_link}")
    if arxiv_link:
        lines.append(f"   📚 arXiv: {arxiv_link}")
    if pdf_link:
        lines.append(f"   📄 PDF: {pdf_link}")
    return "\n".join(lines)


def search_iclr(query, venue="ICLR 2026", status="all", limit=10):
    """搜索 ICLR 论文"""

    # 确定 venue 搜索值
    venue_info = KNOWN_VENUES.get(venue)
    if not venue_info:
        # 未知年份，直接搜索
        venue_vals = [venue]
    else:
        if status == "accepted":
            venue_vals = venue_info["accepted"]
        elif status == "submitted":
            venue_vals = venue_info["submitted"]
        else:
            venue_vals = venue_info["accepted"] + venue_info["submitted"]

    print(f"🔍 搜索 '{query}' 在 {venue} ({status})...", file=sys.stderr)
    print(f"   匹配 venues: {venue_vals}", file=sys.stderr)

    notes = search_openreview(query, venue_vals, limit=limit * 2)

    # 去重过滤（按 forum ID）
    seen = set()
    unique = []
    for n in notes:
        f = n.get("forum", "")
        if f and f not in seen:
            seen.add(f)
            unique.append(n)

    results = unique[:limit]
    print(f"\n📊 共找到 {len(results)} 篇论文\n", file=sys.stderr)

    for i, note in enumerate(results, 1):
        print(f"--- [{i}/{len(results)}] ---")
        print(format_paper(note))
        print()
        sys.stdout.flush()

    return results


def search_arxiv_fallback(query, limit=5):
    """如果 OpenReview 搜不到，fallback 到 arXiv"""
    print("\n📡 通过 arXiv 补充检索...", file=sys.stderr)
    try:
        import arxiv
        search = arxiv.Search(query=query, max_results=limit)
        results = list(search.results())
        for i, r in enumerate(results, 1):
            print(f"--- [{i}/{len(results)}] (arXiv) ---")
            print(f"📄 **{r.title}**")
            authors = ", ".join(str(a) for a in r.authors[:5])
            if len(r.authors) > 5:
                authors += " et al."
            print(f"   👥 {authors}")
            print(f"   📅 {r.published.strftime('%Y-%m-%d')}")
            abs_short = r.summary[:300].replace("\n", " ")
            print(f"   📝 {abs_short}{'...' if len(r.summary) > 300 else ''}")
            print(f"   📚 arXiv: {r.entry_id}")
            print()
            sys.stdout.flush()
        return results
    except ImportError:
        print("  ⚠️ arxiv 包未安装，无法 fallback", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ⚠️ arXiv 查询失败: {e}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(
        description="ICLR / OpenReview 论文搜索工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python3 %(prog)s "multi-granularity memory --venue "ICLR 2026"
  python3 %(prog)s "transformer" --venue "ICLR 2026" --status poster
  python3 %(prog)s "retrieval augmented generation" --all-years
        """,
    )
    parser.add_argument("query", help="搜索关键词")
    parser.add_argument("--venue", "-v", default="ICLR 2026",
                        help="会议名（如 ICLR 2026, NeurIPS 2025），默认 ICLR 2026")
    parser.add_argument("--status", "-s", choices=["all", "accepted", "submitted", "poster", "oral"],
                        default="all",
                        help="论文状态 (all/accepted/submitted/poster/oral)，默认 all")
    parser.add_argument("--limit", "-n", type=int, default=10,
                        help="返回数量 (默认 10)")
    parser.add_argument("--all-years", "-a", action="store_true",
                        help="搜索所有已知 ICLR 年份")
    parser.add_argument("--arxiv-fallback", "-f", action="store_true",
                        help="OpenReview 结果不足时补充 arXiv 结果")

    args = parser.parse_args()

    # 映射 status 参数
    status = args.status
    if status == "poster":
        status = "accepted"
        args.venue_val_override = ["ICLR 2026 Poster", "ICLR 2025 Poster"]
    elif status == "oral":
        status = "accepted"
        args.venue_val_override = ["ICLR 2026 Oral"]

    if args.all_years:
        all_results = []
        for vname in sorted(KNOWN_VENUES.keys(), reverse=True):
            results = search_iclr(args.query, venue=vname, status=status, limit=args.limit)
            all_results.extend(results)
            if len(all_results) >= args.limit:
                break
        if len(all_results) < args.limit and args.arxiv_fallback:
            search_arxiv_fallback(args.query, args.limit - len(all_results))
    else:
        results = search_iclr(args.query, venue=args.venue, status=status, limit=args.limit)
        if len(results) < args.limit and args.arxiv_fallback:
            search_arxiv_fallback(args.query, args.limit - len(results))


if __name__ == "__main__":
    main()
