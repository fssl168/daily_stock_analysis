# -*- coding: utf-8 -*-
"""WebSocket 实时行情通道（T17 架构层，纯新增）.

基于 asyncio + websockets 的通用 WebSocket 客户端通道：

- ``connect(url, auth_token=None)``: 建立连接，成功后自动重发当前订阅代码
- ``subscribe(codes)``: 发送订阅消息并记录代码（断线重连后自动恢复订阅）
- ``run_forever(url=None, auth_token=None)``: 阻塞式运行重连/消费循环，
  供独立线程调用；``stop()`` 可从任意线程安全地终止它
- 连接断开后按 ``ReconnectPolicy`` 指数退避自动重连

回调约定：
- ``on_message(message)``: 收到原始消息时调用（str/bytes，同步或异步均可）
- ``on_disconnect()``: 连接断开时调用，无参数（与架构文档 §2.1 用法一致）

实现依据: docs/architecture/realtime_quant_system_design.md §2.1
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Iterable, Optional, Set

from paper_trading.ws_reconnect import ReconnectPolicy, exponential_backoff

logger = logging.getLogger(__name__)

MessageHandler = Callable[[Any], Any]
DisconnectHandler = Callable[[], Any]


class WebSocketChannel:
    """带指数退避自动重连的 asyncio WebSocket 行情通道."""

    def __init__(
        self,
        watched_codes: Optional[Iterable[str]] = None,
        on_message: Optional[MessageHandler] = None,
        on_disconnect: Optional[DisconnectHandler] = None,
        reconnect_policy: Optional[ReconnectPolicy] = None,
    ) -> None:
        self._codes: Set[str] = set(watched_codes or ())
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._policy = reconnect_policy or ReconnectPolicy()

        self._ws: Optional[Any] = None
        self._url: Optional[str] = None
        self._auth_token: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown = threading.Event()
        self._stop_event = asyncio.Event()
        self._retry = 0
        self._connected = False

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """当前是否处于已连接状态."""
        return self._connected

    async def connect(self, url: str, auth_token: Optional[str] = None) -> Any:
        """建立 WebSocket 连接并返回底层连接对象.

        连接成功后自动重发当前已订阅代码；失败时抛出异常，
        由 ``run_forever`` 的重连循环负责捕获并按策略退避。
        """
        import websockets

        self._url = url
        self._auth_token = auth_token
        if self._ws is not None:
            await self._close_ws()

        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        ws = await websockets.connect(url, extra_headers=headers or None)

        self._ws = ws
        self._connected = True
        if self._policy.reset_on_success:
            self._retry = 0
        if self._codes:
            await self.subscribe(sorted(self._codes))
        return ws

    async def subscribe(self, codes: Iterable[str]) -> None:
        """向已建立连接发送订阅消息，并记录代码以便重连后恢复订阅."""
        if self._ws is None:
            raise RuntimeError("subscribe() requires an active connection (call connect() first)")
        code_list = [str(code) for code in codes]
        self._codes.update(code_list)
        await self._ws.send(json.dumps({"action": "subscribe", "codes": code_list}))

    def run_forever(self, url: Optional[str] = None, auth_token: Optional[str] = None) -> None:
        """阻塞式运行重连/消费循环，直到 ``stop()`` 被调用（适合独立线程）.

        未提供 url 时使用之前 ``connect()`` 传入的 url。
        """
        if url is not None:
            self._url = url
        if auth_token is not None:
            self._auth_token = auth_token
        if self._url is None:
            raise RuntimeError("run_forever() requires a URL (via connect() or url=...)")

        self._shutdown.clear()
        self._stop_event.clear()
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_loop())
        finally:
            self._loop = None
            try:
                pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()

    def stop(self) -> None:
        """请求停止：线程安全，可从任意线程调用.

        会关闭当前连接并唤醒正在退避等待的循环。
        """
        self._shutdown.set()
        loop = self._loop
        if loop is not None:
            try:
                if loop.is_running():
                    # 停止请求必须一次性投递到事件循环线程，避免循环在两次
                    # call_soon_threadsafe 之间退出造成竞态。
                    loop.call_soon_threadsafe(self._request_stop)
                    return
            except RuntimeError:
                pass  # 事件循环已在收尾时关闭，走下方兜底
        self._stop_event.set()
        self._connected = False

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _request_stop(self) -> None:
        """在事件循环线程内执行停止请求（stop 的线程安全入口）."""
        self._stop_event.set()
        self._schedule_ws_close()

    async def _run_loop(self) -> None:
        """核心循环：连接 → 消费消息 → 断开退避重连，直到 stop()."""
        self._loop = asyncio.get_running_loop()
        if self._url is None:
            raise RuntimeError("run_forever() requires a URL (via connect() or url=...)")
        while not self._shutdown.is_set():
            try:
                await self.connect(self._url, self._auth_token)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WebSocket 连接失败: %s", exc)
                await self._handle_disconnect()
                self._retry += 1
                if not await self._wait_backoff():
                    break
                continue

            try:
                async for message in self._ws:
                    if self._stop_event.is_set():
                        break
                    await self._dispatch_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WebSocket 接收异常: %s", exc)
            finally:
                await self._close_ws()

            if self._stop_event.is_set():
                break
            await self._handle_disconnect()
            self._retry += 1
            if not await self._wait_backoff():
                break
        await self._close_ws()

    async def _handle_disconnect(self) -> None:
        """触发断开回调（回调异常只记录日志，不中断重连循环）."""
        self._connected = False
        handler = self._on_disconnect
        if handler is None:
            return
        try:
            result = handler()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("on_disconnect callback failed")

    async def _dispatch_message(self, message: Any) -> None:
        """把收到的消息交给 on_message 回调（同步/异步均可，异常不抛出）."""
        handler = self._on_message
        if handler is None:
            return
        try:
            result = handler(message)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("on_message callback failed")

    async def _wait_backoff(self) -> bool:
        """按策略等待退避；返回 False 表示应停止重连（停止请求或达到 max_retries）."""
        if self._stop_event.is_set():
            return False
        if self._policy.max_retries is not None and self._retry > self._policy.max_retries:
            logger.warning("达到最大重试次数 %s，停止重连", self._policy.max_retries)
            return False
        delay = exponential_backoff(self._policy, self._retry - 1)
        logger.warning("WebSocket 断开，%.1fs 后进行第 %d 次重连", delay, self._retry)
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return True
        return False

    async def _close_ws(self) -> None:
        """关闭当前连接（幂等，异常静默）."""
        ws = self._ws
        self._ws = None
        self._connected = False
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    def _schedule_ws_close(self) -> None:
        """在事件循环线程内调度关闭连接（供 stop 跨线程调用）."""
        if self._ws is not None:
            asyncio.create_task(self._close_ws())
