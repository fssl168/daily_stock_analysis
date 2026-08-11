# -*- coding: utf-8 -*-
"""
Search tools — wraps SearchService methods as agent-callable tools.

Tools:
- search_stock_news: search latest stock news
- search_comprehensive_intel: multi-dimensional intelligence search
"""

import logging

from src.agent.tools.registry import ToolParameter, ToolDefinition

logger = logging.getLogger(__name__)


def _get_db():
    """Lazy import for DatabaseManager."""
    from src.storage import get_db
    return get_db()


def _get_search_service():
    """Return shared SearchService singleton."""
    from src.search_service import get_search_service
    return get_search_service()


def _canonical_search_code(stock_code: str) -> str:
    from data_provider.base import canonical_stock_code, normalize_stock_code

    return canonical_stock_code(normalize_stock_code(str(stock_code or "").strip()))


def _persist_news_response(
    *,
    stock_code: str,
    stock_name: str,
    dimension: str,
    response,
) -> None:
    """Best-effort news persistence for Agent search tools."""
    if not response or not getattr(response, "success", False) or not getattr(response, "results", None):
        return

    code = _canonical_search_code(stock_code)
    try:
        saved_count = _get_db().save_news_intel(
            code=code,
            name=stock_name,
            dimension=dimension,
            query=response.query,
            response=response,
            query_context=None,
        )
        logger.info(
            "Agent news intel persisted for %s (dimension=%s, new_records=%s)",
            code,
            dimension,
            saved_count,
        )
    except Exception as exc:
        logger.warning(
            "Agent news intel persistence failed for %s (dimension=%s): %s",
            code,
            dimension,
            exc,
        )


def _handle_search_stock_news(stock_code: str, stock_name: str) -> dict:
    """Search latest news for a stock."""
    service = _get_search_service()

    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    response = service.search_stock_news(stock_code, stock_name, max_results=5)

    if not response.success:
        return {
            "query": response.query,
            "success": False,
            "error": response.error_message,
        }

    _persist_news_response(
        stock_code=stock_code,
        stock_name=stock_name,
        dimension="latest_news",
        response=response,
    )

    return {
        "query": response.query,
        "provider": response.provider,
        "success": True,
        "results_count": len(response.results),
        "results": [
            {
                "title": r.title,
                "snippet": r.snippet,
                "url": r.url,
                "source": r.source,
                "published_date": r.published_date,
            }
            for r in response.results
        ],
    }


search_stock_news_tool = ToolDefinition(
    name="search_stock_news",
    description="Search for the latest news articles about a specific stock. "
                "Requires both stock_code and stock_name for accurate search. "
                "Returns news titles, snippets, sources, and URLs.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_stock_news,
    category="search",
)


# ============================================================
# search_comprehensive_intel
# ============================================================

def _handle_search_comprehensive_intel(stock_code: str, stock_name: str) -> dict:
    """Multi-dimensional intelligence search."""
    service = _get_search_service()

    if not service.is_available:
        return {"error": "No search engine available (no API keys configured)"}

    intel_results = service.search_comprehensive_intel(
        stock_code=stock_code,
        stock_name=stock_name,
        max_searches=6,
    )

    if not intel_results:
        return {"error": "Comprehensive intel search returned no results"}

    # Format into readable report
    report = service.format_intel_report(intel_results, stock_name)

    # Also return structured data
    dimensions = {}
    for dim_name, response in intel_results.items():
        if response and response.success:
            _persist_news_response(
                stock_code=stock_code,
                stock_name=stock_name,
                dimension=dim_name,
                response=response,
            )
            dimensions[dim_name] = {
                "query": response.query,
                "results_count": len(response.results),
                "results": [
                    {
                        "title": r.title,
                        "snippet": r.snippet,
                        "source": r.source,
                    }
                    for r in response.results[:3]  # limit to 3 per dimension to save tokens
                ],
            }

    return {
        "report": report,
        "dimensions": dimensions,
    }


search_comprehensive_intel_tool = ToolDefinition(
    name="search_comprehensive_intel",
    description="Multi-dimensional intelligence search: latest news, market analysis, "
                "risk checking, earnings outlook, and industry trends for a stock. "
                "Returns a formatted report and structured results.",
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="Stock code, e.g., '600519'",
        ),
        ToolParameter(
            name="stock_name",
            type="string",
            description="Stock name in Chinese, e.g., '贵州茅台'",
        ),
    ],
    handler=_handle_search_comprehensive_intel,
    category="search",
)


ALL_SEARCH_TOOLS = [
    search_stock_news_tool,
    search_comprehensive_intel_tool,
]


# ============================================================
# search_papers — arXiv / OpenAlex 学术论文搜索（中英文）
# ============================================================

_ARXIV_API = "https://export.arxiv.org/api/query"
_OPENALEX_API = "https://api.openalex.org/works"
_CROSSREF_API = "https://api.crossref.org/works"
_ARXIV_MAX_RESULTS = 10


def _fetch_url(url: str, timeout: int = 30) -> bytes:
    """直连优先，失败走环境变量配置的代理；返回响应字节。带 User-Agent（OpenAlex 要求，否则 429）。

    代理从标准环境变量读取（HTTP_PROXY/HTTPS_PROXY，兼容小写变体），与 src/config.py 的约定一致；
    未配置代理时仅直连。
    """
    import os
    import time
    import urllib.request

    headers = {
        "User-Agent": "DSA-Agent/1.0 (https://github.com/fssl168/daily_stock_analysis; academic paper search)",
        "Accept": "application/json, application/atom+xml, text/xml, */*",
    }
    http_proxy = (
        os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or ""
    )
    last_err = None
    # 直连优先，失败再走代理（避免代理不可用时拖垮直连路径）
    proxies = [None]
    if http_proxy:
        proxies.append(http_proxy)
    for proxy in proxies:
        try:
            if proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
                )
            else:
                opener = urllib.request.build_opener()
            req = urllib.request.Request(url, headers=headers)
            with opener.open(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 — 网络错误统一降级
            last_err = exc
            # 429 限流：短暂等待后重试一次
            if getattr(exc, "code", None) == 429:
                time.sleep(2.0)
                continue
            continue
    raise OSError(f"request failed: {last_err}")


def _rebuild_openalex_abstract(inverted_index) -> str:
    """OpenAlex 摘要为倒排索引 {word: [positions]} → 重建文本。"""
    if not inverted_index:
        return ""
    positions = {}
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions[idx] = word
    return " ".join(positions[i] for i in sorted(positions))


def _search_openalex(query: str, max_results: int, language: str = "all") -> dict:
    """OpenAlex 学术搜索：全球文献（含中文期刊），免费无需 key。"""
    import json
    import urllib.parse

    params = {
        "search": query,
        "per-page": max_results,
        "sort": "relevance_score:desc",
    }
    if language in ("zh", "en"):
        params["filter"] = f"language:{language}"
    url = f"{_OPENALEX_API}?{urllib.parse.urlencode(params)}"

    try:
        data = json.loads(_fetch_url(url).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"OpenAlex request failed: {exc}"}

    papers = []
    for work in data.get("results", [])[:max_results]:
        title = (work.get("title") or "").strip()
        if not title:
            continue
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in work.get("authorships", [])[:4]
        ]
        abstract = _rebuild_openalex_abstract(work.get("abstract_inverted_index"))
        source_name = ""
        loc = work.get("primary_location") or {}
        src = loc.get("source") or {}
        source_name = src.get("display_name") or ""
        papers.append(
            {
                "title": title,
                "authors": [a for a in authors if a],
                "abstract": abstract[:300],
                "url": work.get("doi") or work.get("id") or "",
                "source": source_name or "OpenAlex",
            }
        )

    result = {
        "query": query,
        "count": len(papers),
        "papers": papers,
    }
    if not papers:
        result["hint"] = "未找到相关论文，可尝试更换关键词（英文效果更佳）。"
    return result


def _strip_jats_abstract(abstract: str) -> str:
    """Crossref 摘要为 JATS XML（<jats:p>…</jats:p>）→ 去标签。"""
    import re

    if not abstract:
        return ""
    text = re.sub(r"<[^>]+>", " ", abstract)
    return re.sub(r"\s+", " ", text).strip()


def _search_crossref(query: str, max_results: int) -> dict:
    """Crossref 学术搜索：全球期刊文献（含中文期刊），免费公开无需 key。"""
    import json
    import urllib.parse

    params = {
        "query": query,
        "rows": max_results,
        "select": "title,author,DOI,abstract,container-title,issued",
    }
    url = f"{_CROSSREF_API}?{urllib.parse.urlencode(params)}"

    try:
        data = json.loads(_fetch_url(url).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Crossref request failed: {exc}"}

    papers = []
    for item in data.get("message", {}).get("items", [])[:max_results]:
        title = ((item.get("title") or [""])[0] or "").strip()
        if not title:
            continue
        authors = []
        for a in item.get("author", [])[:4]:
            name = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if name:
                authors.append(name)
        journal = (item.get("container-title") or [""])[0] or ""
        issued = item.get("issued", {}).get("date-parts", [[None]])[0][0]
        doi = item.get("DOI", "")
        papers.append(
            {
                "title": title,
                "authors": authors,
                "abstract": _strip_jats_abstract(item.get("abstract") or "")[:300],
                "url": f"https://doi.org/{doi}" if doi else "",
                "source": journal or "Crossref",
                "year": str(issued) if issued else "",
            }
        )

    result = {
        "query": query,
        "count": len(papers),
        "papers": papers,
    }
    if not papers:
        result["hint"] = "未找到相关论文，可尝试更换关键词。"
    return result


def _handle_search_papers(query: str, max_results: int = 5, source: str = "auto",
                          language: str = "all") -> dict:
    """Search academic papers by topic (中英文论文资料查询).

    - source="arxiv": 英文文献库 arXiv（预印本，AI/CS 领域）
    - source="openalex": OpenAlex 全球学术库（含中文期刊；免费额度有限，429 时自动降级 Crossref）
    - source="crossref": Crossref 期刊库（免费公开，含中文期刊，中英文均可）
    - source="auto"（默认）: 查询含中文 → openalex → crossref 兜底；
      否则 arxiv 优先，失败再降级 openalex → crossref

    Returns paper titles, authors, abstracts (truncated), and URLs.
    Pure stdlib (urllib + ElementTree / json) — no third-party dependencies.
    """
    import re
    import urllib.parse
    import xml.etree.ElementTree as ET

    query = str(query or "").strip()
    if not query:
        return {"error": "query is required"}

    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 5
    max_results = max(1, min(max_results, _ARXIV_MAX_RESULTS))

    source = str(source or "auto").strip().lower()
    language = str(language or "all").strip().lower()
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in query)

    # 中文 / 显式 openalex / crossref 路径：openalex 免费额度可能耗尽 → crossref 兜底
    if source in ("openalex", "crossref") or (source == "auto" and has_cjk):
        if source == "crossref":
            return _search_crossref(query, max_results)
        r = _search_openalex(query, max_results, language)
        if "error" in r or not r.get("papers"):
            return _search_crossref(query, max_results)
        return r

    # ── arXiv 路径（英文） ──
    # 按词 AND 查询（精确短语 all:"..." 几乎匹配不到多词查询）
    terms = [t for t in re.split(r"\s+", query.strip()) if t]
    search_query = " AND ".join(f"all:{t}" for t in terms) if len(terms) > 1 else f'all:"{query}"'
    params = urllib.parse.urlencode(
        {
            "search_query": search_query,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
    )
    url = f"{_ARXIV_API}?{params}"

    try:
        xml_data = _fetch_url(url)
    except Exception as exc:  # noqa: BLE001
        # 英文查询 arXiv 失败 → 降级 openalex → crossref（覆盖更广）
        if source == "auto":
            r = _search_openalex(query, max_results, language)
            if "error" in r or not r.get("papers"):
                return _search_crossref(query, max_results)
            return r
        return {"error": f"arXiv request failed: {exc}"}

    try:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_data)
        papers = []
        for entry in root.findall("atom:entry", ns)[:max_results]:
            title = re.sub(r"\s+", " ", (entry.findtext("atom:title", "", ns) or "").strip())
            summary = re.sub(r"\s+", " ", (entry.findtext("atom:summary", "", ns) or "").strip())
            authors = [
                a.findtext("atom:name", "", ns)
                for a in entry.findall("atom:author", ns)
            ]
            link = entry.findtext("atom:id", "", ns)
            papers.append(
                {
                    "title": title,
                    "authors": authors[:4],
                    "abstract": summary[:300],
                    "url": link,
                    "source": "arXiv",
                }
            )
    except Exception as exc:  # noqa: BLE001 — 解析失败返回错误
        return {"error": f"arXiv response parse failed: {exc}"}

    result = {
        "query": query,
        "count": len(papers),
        "papers": papers,
    }
    # arXiv 以英文论文为主：中文关键词通常搜不到，附提示
    if not papers and has_cjk:
        result["hint"] = (
            "arXiv 主要收录英文论文，中文关键词通常无结果。"
            "已可尝试 source=openalex 查询中文论文。"
        )
    return result


search_papers_tool = ToolDefinition(
    name="search_papers",
    description="Search academic papers (中英文论文资料查询). Supports both English "
                "arXiv preprints and OpenAlex global literature including Chinese "
                "journals. Use for research questions about AI/AGI/LLM/quantitative "
                "finance and any literature search. Chinese queries automatically "
                "route to OpenAlex; English queries prefer arXiv with OpenAlex fallback.",
    parameters=[
        ToolParameter(
            name="query",
            type="string",
            description="Search topic, e.g., 'AGI' / 'reinforcement learning' / '强化学习'",
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="Max number of papers to return (default: 5, max: 10)",
            required=False,
            default=5,
        ),
        ToolParameter(
            name="source",
            type="string",
            description="Paper source: 'arxiv' (English preprints), 'openalex' "
                        "(global literature incl. Chinese), 'crossref' (journal "
                        "literature incl. Chinese), 'auto' (default: Chinese query "
                        "→ openalex/crossref, English → arxiv)",
            required=False,
            default="auto",
            enum=["auto", "arxiv", "openalex", "crossref"],
        ),
        ToolParameter(
            name="language",
            type="string",
            description="Language filter for openalex: 'zh' / 'en' / 'all' (default: all)",
            required=False,
            default="all",
            enum=["all", "zh", "en"],
        ),
    ],
    handler=_handle_search_papers,
    category="search",
)

ALL_SEARCH_TOOLS.append(search_papers_tool)
