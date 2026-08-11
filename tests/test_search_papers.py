# -*- coding: utf-8 -*-
"""Tests for Agent search_papers (arXiv) tool."""

import unittest
from unittest.mock import MagicMock, patch

from src.agent.tools.search_tools import (
    _handle_search_papers,
    search_papers_tool,
)

ARXIV_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2311.02462v5</id>
    <title>Levels of AGI for Operationalizing Progress on the Path to AGI</title>
    <author><name>Meredith Ringel Morris</name></author>
    <author><name>Jascha Sohl-Dickstein</name></author>
    <summary>We propose a framework for classifying AGI models and their precursors.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2506.13134v1</id>
    <title>Quantum AGI: Ontological Foundations</title>
    <author><name>Elija Perrier</name></author>
    <summary>Ontological foundations for quantum AGI.</summary>
  </entry>
</feed>
"""

CROSSREF_JSON = {
    "message": {
        "items": [
            {
                "title": ["强化学习与深度学习结合的多智能体系统研究"],
                "author": [{"given": "张", "family": "三"}, {"given": "李", "family": "四"}],
                "DOI": "10.1234/cn.example.2025.001",
                "container-title": ["电子通信与计算机科学"],
                "issued": {"date-parts": [[2025, 3]]},
                "abstract": "<jats:p>提出一种强化学习与深度学习的结合方法。</jats:p>",
            },
            {
                "title": ["A Survey of Reinforcement Learning"],
                "author": [{"given": "Jane", "family": "Doe"}],
                "DOI": "10.5678/example.2024.002",
                "container-title": ["Journal of AI"],
                "issued": {"date-parts": [[2024]]},
                "abstract": "",
            },
        ]
    }
}


class SearchPapersToolTest(unittest.TestCase):
    def test_tool_registered_in_schema(self) -> None:
        """ToolDefinition 形状正确：name/参数/分类。"""
        tool = search_papers_tool
        self.assertEqual(tool.name, "search_papers")
        self.assertEqual(tool.category, "search")
        param_names = [p.name for p in tool.parameters]
        self.assertIn("query", param_names)
        self.assertIn("max_results", param_names)
        openai_tool = tool.to_openai_tool()
        self.assertEqual(openai_tool["function"]["name"], "search_papers")
        props = openai_tool["function"]["parameters"]["properties"]
        self.assertEqual(props["query"]["type"], "string")
        self.assertEqual(props["max_results"]["type"], "integer")

    def test_handler_parses_arxiv_response(self) -> None:
        """mock 网络：正常解析出论文列表。"""
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = ARXIV_XML

        with patch("urllib.request.build_opener", return_value=opener):
            result = _handle_search_papers("AGI", max_results=5)

        self.assertEqual(result["query"], "AGI")
        self.assertEqual(result["count"], 2)
        first = result["papers"][0]
        self.assertIn("Levels of AGI", first["title"])
        self.assertIn("Morris", first["authors"][0])
        self.assertIn("2311.02462", first["url"])

    def test_handler_empty_query_returns_error(self) -> None:
        result = _handle_search_papers("", max_results=5)
        self.assertIn("error", result)

    def test_handler_clamps_max_results(self) -> None:
        """max_results 钳制在 [1, 10]。"""
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = ARXIV_XML

        with patch("urllib.request.build_opener", return_value=opener):
            result = _handle_search_papers("AGI", max_results=99)

        # clamp 到 10，但 mock 数据只有 2 条
        self.assertEqual(result["count"], 2)
        # 验证请求 URL 里 max_results=10（Request 对象取 full_url）
        call_url = opener.open.call_args[0][0]
        url_str = call_url.full_url if hasattr(call_url, "full_url") else str(call_url)
        self.assertIn("max_results=10", url_str)

    def test_handler_network_failure_returns_error(self) -> None:
        """网络失败（直连+代理+全部兜底源失败）→ 返回 error 而非抛异常。"""
        def _raise(*_args, **_kwargs):
            raise OSError("connection refused")

        with patch("urllib.request.build_opener", side_effect=_raise):
            result = _handle_search_papers("AGI", max_results=5)

        self.assertIn("error", result)
        # 英文 auto 链：arxiv → openalex → crossref 全部失败
        self.assertIn("Crossref", result["error"])

    # ── 中英文查询（Crossref） ────────────────────────────────

    def test_chinese_query_routes_to_crossref(self) -> None:
        """中文 query：openalex 不可用（429/空）→ crossref 兜底返回中文论文。"""
        def _fake_fetch(url: str, timeout: int = 30) -> bytes:
            if "openalex" in url:
                raise OSError("HTTP Error 429: Too Many Requests")
            return __import__("json").dumps(CROSSREF_JSON).encode("utf-8")

        with patch("src.agent.tools.search_tools._fetch_url", side_effect=_fake_fetch):
            result = _handle_search_papers("强化学习", max_results=2)

        self.assertNotIn("error", result)
        self.assertEqual(result["count"], 2)
        first = result["papers"][0]
        self.assertIn("强化学习", first["title"])
        self.assertEqual(first["source"], "电子通信与计算机科学")
        self.assertEqual(first["year"], "2025")
        self.assertIn("doi.org/10.1234", first["url"])
        # JATS 标签被清洗
        self.assertNotIn("<jats", first["abstract"])

    def test_explicit_crossref_source(self) -> None:
        """显式 source=crossref：直接走 Crossref，不经过 openalex。"""
        def _fake_fetch(url: str, timeout: int = 30) -> bytes:
            return __import__("json").dumps(CROSSREF_JSON).encode("utf-8")

        with patch("src.agent.tools.search_tools._fetch_url", side_effect=_fake_fetch):
            result = _handle_search_papers("强化学习", max_results=2, source="crossref")

        self.assertNotIn("error", result)
        self.assertEqual(result["count"], 2)

    def test_english_arxiv_preferred_then_crossref_fallback(self) -> None:
        """英文 query：arxiv 失败 → auto 降级 openalex → crossref。"""
        def _fake_fetch(url: str, timeout: int = 30) -> bytes:
            if "arxiv" in url:
                raise OSError("arxiv timeout")
            if "openalex" in url:
                raise OSError("HTTP Error 429: Too Many Requests")
            return __import__("json").dumps(CROSSREF_JSON).encode("utf-8")

        with patch("src.agent.tools.search_tools._fetch_url", side_effect=_fake_fetch):
            result = _handle_search_papers("reinforcement learning", max_results=2)

        self.assertNotIn("error", result)
        self.assertEqual(result["count"], 2)


if __name__ == "__main__":
    unittest.main()
