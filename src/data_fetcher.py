# -*- coding: utf-8 -*-
"""Multi-source data fetcher with priority-based fallback for paper trading.

This module provides a unified interface to retrieve stock data from multiple
data sources, trying them in order of priority and falling back when one is
unavailable. This ensures that the MarketListener can continue operating even
when certain data sources fail.

Supported data sources (in default order):
1. tickflow - TickFlow data provider (highest priority for A-shares)
2. tushare - Tushare Pro (comprehensive financial data)
3. yfinance - Yahoo Finance (global markets)
4. akshare - AkShare (open source financial data)
"""

from typing import Any, Optional, List, Tuple, Dict
import pandas as pd
import logging
import time  # Added for cache timestamp
from functools import lru_cache

logger = logging.getLogger(__name__)


class MultiSourceDataFetcher:
    """统一的多数据源行情获取器，支持按优先级列表自动降级.

    使用示例:
        fetcher = MultiSourceDataFetcher(source_priority=["tickflow", "tushare", "yfinance"])
        df = fetcher.get_daily_historical("600519", days=120)
    """

    DEFAULT_PRIORITY = ["tickflow", "tushare", "yfinance", "akshare"]

    def __init__(self, source_priority: Optional[List[str]] = None, cache_ttl: int = 60):
        self.source_priority = source_priority or self.DEFAULT_PRIORITY
        self._sources_cache = {}  # 懒加载各数据源适配器实例
        self._cache = {}          # 结果缓存字典
        self._cache_timestamps = {}  # 时间戳字典
        self.cache_ttl = cache_ttl  # 缓存有效期（秒）

    def _get_source_adapter(self, source_name: str) -> Optional[Any]:
        """获取指定名称的数据源适配器实例，单例缓存.

        如果之前尝试过失败会记录为 None，避免重复失败的开销.
        """
        if source_name in self._sources_cache:
            cached = self._sources_cache[source_name]
            if cached is not None:
                return cached

        try:
            # 根据源名称导入对应的 fetcher 类
            if source_name == "tickflow":
                from data_provider.tickflow_fetcher import TickFlowFetcher
                adapter = TickFlowFetcher()
            elif source_name == "tushare":
                from data_provider.tushare_fetcher import TushareFetcher
                adapter = TushareFetcher()
            elif source_name == "yfinance":
                from data_provider.yfinance_fetcher import YFinanceFetcher
                adapter = YFinanceFetcher()
            elif source_name == "akshare":
                from data_provider.akshare_fetcher import AkShareFetcher
                adapter = AkShareFetcher()
            else:
                logger.warning("Unknown data source: %s", source_name)
                adapter = None

            self._sources_cache[source_name] = adapter
            return adapter

        except Exception as e:
            logger.debug("Failed to instantiate data source '%s': %s", source_name, e)
            self._sources_cache[source_name] = None  # 标记失败，不再重试
            return None

    def _is_cache_valid(self, key: str) -> bool:
        """检查缓存是否过期."""
        now = time.time()
        return key in self._cache and (now - self._cache_timestamps[key]) < self.cache_ttl

    def _set_cache(self, key: Any, value: Any):
        """设置缓存."""
        self._cache[key] = value
        self._cache_timestamps[key] = time.time()

    def get_daily_historical(self, code: str, days: int = 120) -> Optional[pd.DataFrame]:
        """按优先级尝试多个数据源获取日 K 线数据，返回第一个成功的 DataFrame.

        每个数据源的 get_daily_historical 方法应接受 (code: str, days: int)
        并返回 pd.DataFrame 或 None。
        """
        cache_key = f"hist_{code}_{days}"
        if self._is_cache_valid(cache_key):
            return self._cache.get(cache_key)

        for source_name in self.source_priority:
            adapter = self._get_source_adapter(source_name)
            if adapter is None:
                continue

            try:
                # 调用统一的接口获取日线数据
                df = adapter.get_daily_historical(code, days)
                if df is not None and not df.empty and len(df) >= min(10, days):
                    logger.info("Daily historical data for %s fetched from %s", code, source_name)
                    self._set_cache(cache_key, df)
                    return df
            except Exception as e:
                logger.debug("Source '%s' failed for %s (days=%d): %s", source_name, code, days, e)
                continue

        logger.warning("All data sources failed for %s (days=%d)", code, days)
        self._set_cache(cache_key, None)  # 缓存空结果
        return None

    def get_realtime_quote(self, code: str) -> Optional[Dict]:
        """获取实时行情报价，同样支持优先级降级.

        期望返回的字典包含至少 'price' 字段（float），以及可选的 'code', 'volume' 等.
        """
        cache_key = f"real_{code}"
        if self._is_cache_valid(cache_key):
            return self._cache.get(cache_key)

        for source_name in self.source_priority:
            adapter = self._get_source_adapter(source_name)
            if adapter is None:
                continue

            try:
                quote = adapter.get_realtime_quote(code)
                if quote is not None and isinstance(quote, dict) and 'price' in quote:
                    logger.info("Realtime quote for %s from %s", code, source_name)
                    self._set_cache(cache_key, quote)
                    return quote
            except Exception as e:
                logger.debug("Source '%s' realtime failed for %s: %s", source_name, code, e)
                continue

        return None

    def get_kline_with_source(self, code: str, period: str = "1d") -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        """返回获取到的数据框和实际使用的数据源名称，便于调试.

        Returns:
            (DataFrame, source_name) tuple, where source_name is the string name
            of the data source that successfully provided data, or (None, None) if all failed.
        """
        for source_name in self.source_priority:
            adapter = self._get_source_adapter(source_name)
            if adapter is None:
                continue

            try:
                df = adapter.get_kline(code, period)
                if df is not None and not df.empty:
                    return df, source_name
            except Exception as e:
                logger.debug("Source '%s' kline failed for %s: %s", source_name, code, e)
                continue
        return None, None

    # Alias for compatibility with existing code expectations
    get_daily_data = get_daily_historical
