# -*- coding: utf-8 -*-
"""T17 WebSocketChannel / ReconnectPolicy 单元测试.

不依赖真实 WebSocket 服务：连接通过 mock 的 websockets.connect 注入，
重连 / 订阅 / 断开回调 / stop 全部在本地事件循环内验证。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch

import pytest

from paper_trading.ws_channel import WebSocketChannel
from paper_trading.ws_reconnect import ReconnectPolicy, exponential_backoff


# ----------------------------------------------------------------------
# Fake WebSocket 连接
# ----------------------------------------------------------------------

class FakeWS:
    """模拟 websockets 连接对象：可发送/关闭，并按预设消息序列迭代."""

    def __init__(self, messages=None, block=False, fail_iter=None):
        self.sent = []
        self.closed = False
        self._messages = list(messages or [])
        self._release = asyncio.Event()
        self._block = block
        self._fail_iter = fail_iter

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True
        self._release.set()

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        if self._fail_iter is not None:
            raise self._fail_iter
        if self._block:
            await self._release.wait()
            return
        for message in self._messages:
            yield message


def _connect_ok(ws):
    """构造 websockets.connect 替身：固定返回一个可 await 的连接."""

    async def _connect(url, **kwargs):
        return ws

    return _connect


def _connect_sequence(*results):
    """构造 websockets.connect 替身：依次返回结果（Exception 实例表示抛出）."""

    iterator = iter(results)

    async def _connect(url, **kwargs):
        result = next(iterator)
        if isinstance(result, BaseException):
            raise result
        return result

    return _connect


# ----------------------------------------------------------------------
# ReconnectPolicy / exponential_backoff（纯逻辑）
# ----------------------------------------------------------------------

class TestReconnectPolicy:
    def test_defaults(self):
        policy = ReconnectPolicy()
        assert policy.initial_backoff == 1.0
        assert policy.multiplier == 2.0
        assert policy.max_backoff == 30.0
        assert policy.reset_on_success is True
        assert policy.max_retries is None

    def test_exponential_backoff_sequence(self):
        policy = ReconnectPolicy()
        assert exponential_backoff(policy, 0) == 1.0
        assert exponential_backoff(policy, 1) == 2.0
        assert exponential_backoff(policy, 2) == 4.0
        assert exponential_backoff(policy, 3) == 8.0

    def test_exponential_backoff_caps_at_max(self):
        policy = ReconnectPolicy(initial_backoff=1.0, multiplier=2.0, max_backoff=30.0)
        assert exponential_backoff(policy, 5) == 30.0  # 32 被截断到 30
        assert exponential_backoff(policy, 100) == 30.0

    def test_custom_policy(self):
        policy = ReconnectPolicy(
            initial_backoff=0.5,
            multiplier=3.0,
            max_backoff=10.0,
            reset_on_success=False,
            max_retries=3,
        )
        assert exponential_backoff(policy, 0) == 0.5
        assert exponential_backoff(policy, 2) == 4.5
        assert exponential_backoff(policy, 10) == 10.0
        assert policy.reset_on_success is False
        assert policy.max_retries == 3

    def test_invalid_policy_values(self):
        with pytest.raises(ValueError):
            ReconnectPolicy(initial_backoff=-1)
        with pytest.raises(ValueError):
            ReconnectPolicy(multiplier=0)
        with pytest.raises(ValueError):
            ReconnectPolicy(multiplier=-2)
        with pytest.raises(ValueError):
            ReconnectPolicy(max_backoff=-1)
        with pytest.raises(ValueError):
            ReconnectPolicy(max_retries=-1)

    def test_negative_retry_raises(self):
        with pytest.raises(ValueError):
            exponential_backoff(ReconnectPolicy(), -1)


# ----------------------------------------------------------------------
# WebSocketChannel：连接与订阅
# ----------------------------------------------------------------------

class TestWebSocketChannelConnect(unittest.IsolatedAsyncioTestCase):
    async def test_connect_opens_and_subscribes(self):
        fake = FakeWS()
        channel = WebSocketChannel(watched_codes=["600519", "000001"])
        with patch("websockets.connect", side_effect=_connect_ok(fake)) as mock_connect:
            ws = await channel.connect("wss://example.com/ws")
        assert ws is fake
        mock_connect.assert_called_once()
        assert channel.connected is True
        assert len(fake.sent) == 1
        payload = json.loads(fake.sent[0])
        assert payload["action"] == "subscribe"
        assert payload["codes"] == ["000001", "600519"]

    async def test_connect_passes_auth_header(self):
        fake = FakeWS()
        channel = WebSocketChannel()
        with patch("websockets.connect", side_effect=_connect_ok(fake)) as mock_connect:
            await channel.connect("wss://example.com/ws", auth_token="secret")
        _, kwargs = mock_connect.call_args
        assert kwargs["extra_headers"] == {"Authorization": "Bearer secret"}

    async def test_connect_without_auth_passes_no_headers(self):
        fake = FakeWS()
        channel = WebSocketChannel()
        with patch("websockets.connect", side_effect=_connect_ok(fake)) as mock_connect:
            await channel.connect("wss://example.com/ws")
        _, kwargs = mock_connect.call_args
        assert kwargs.get("extra_headers") is None

    async def test_connect_failure_raises(self):
        channel = WebSocketChannel()
        with patch("websockets.connect", side_effect=_connect_sequence(ConnectionError("boom"))):
            with pytest.raises(ConnectionError):
                await channel.connect("wss://example.com/ws")
        assert channel.connected is False

    async def test_connect_closes_previous_connection(self):
        first = FakeWS()
        second = FakeWS()
        channel = WebSocketChannel()
        with patch("websockets.connect", side_effect=_connect_sequence(first, second)):
            await channel.connect("wss://a")
            await channel.connect("wss://b")
        assert first.closed is True
        assert second.closed is False
        assert channel._ws is second

    async def test_subscribe_before_connect_raises(self):
        channel = WebSocketChannel()
        with pytest.raises(RuntimeError):
            await channel.subscribe(["600519"])

    async def test_subscribe_sends_and_records_codes(self):
        fake = FakeWS()
        channel = WebSocketChannel(watched_codes=["600519"])
        with patch("websockets.connect", side_effect=_connect_ok(fake)):
            await channel.connect("wss://example.com/ws")
        fake.sent.clear()
        await channel.subscribe(["000001", "600519"])
        assert len(fake.sent) == 1
        payload = json.loads(fake.sent[0])
        assert set(payload["codes"]) == {"000001", "600519"}
        assert channel._codes == {"000001", "600519"}


# ----------------------------------------------------------------------
# WebSocketChannel：重连循环 / 回调 / stop
# ----------------------------------------------------------------------

class TestWebSocketChannelLoop(unittest.IsolatedAsyncioTestCase):
    async def test_run_loop_without_url_raises(self):
        channel = WebSocketChannel()
        with pytest.raises(RuntimeError):
            await channel._run_loop()

    async def test_messages_dispatched_to_on_message(self):
        received = []
        fake = FakeWS(messages=["m1", "m2"])
        channel = WebSocketChannel(watched_codes=["600519"], on_message=received.append)
        channel._url = "wss://example.com/ws"

        async def _stop():
            await asyncio.sleep(0.02)
            channel.stop()

        with patch("websockets.connect", side_effect=_connect_ok(fake)):
            await asyncio.wait_for(asyncio.gather(channel._run_loop(), _stop()), timeout=5.0)
        assert received == ["m1", "m2"]
        assert fake.closed is True
        assert channel.connected is False

    async def test_no_callbacks_safe(self):
        fake = FakeWS(messages=["x"])
        channel = WebSocketChannel(reconnect_policy=ReconnectPolicy(max_retries=0))
        channel._url = "wss://example.com/ws"
        with patch("websockets.connect", side_effect=_connect_ok(fake)):
            await asyncio.wait_for(channel._run_loop(), timeout=5.0)
        assert channel.connected is False
        assert fake.closed is True

    async def test_async_callbacks_are_awaited(self):
        received = []

        async def on_message(msg):
            received.append(("m", msg))

        async def on_disconnect():
            received.append(("d", None))

        fake = FakeWS(messages=["x"])
        channel = WebSocketChannel(
            watched_codes=["600519"],
            on_message=on_message,
            on_disconnect=on_disconnect,
            reconnect_policy=ReconnectPolicy(max_retries=0),
        )
        channel._url = "wss://example.com/ws"
        with patch("websockets.connect", side_effect=_connect_ok(fake)):
            await asyncio.wait_for(channel._run_loop(), timeout=5.0)
        assert ("m", "x") in received
        assert ("d", None) in received

    async def test_callback_exception_does_not_kill_loop(self):
        received = []
        disconnects = []

        def on_message(msg):
            received.append(msg)
            if msg == "boom":
                raise RuntimeError("handler bug")

        fake = FakeWS(messages=["boom", "ok"])
        channel = WebSocketChannel(
            on_message=on_message,
            on_disconnect=lambda: disconnects.append("dc"),
            reconnect_policy=ReconnectPolicy(max_retries=0),
        )
        channel._url = "wss://example.com/ws"
        with self.assertLogs("paper_trading.ws_channel", level="ERROR"):
            with patch("websockets.connect", side_effect=_connect_ok(fake)):
                await asyncio.wait_for(channel._run_loop(), timeout=5.0)
        assert received == ["boom", "ok"]
        assert disconnects == ["dc"]

    async def test_disconnect_callback_exception_is_swallowed(self):
        fake = FakeWS()

        def bad_disconnect():
            raise RuntimeError("disconnect handler bug")

        channel = WebSocketChannel(
            on_disconnect=bad_disconnect,
            reconnect_policy=ReconnectPolicy(max_retries=0),
        )
        channel._url = "wss://example.com/ws"
        with self.assertLogs("paper_trading.ws_channel", level="ERROR"):
            with patch("websockets.connect", side_effect=_connect_ok(fake)):
                await asyncio.wait_for(channel._run_loop(), timeout=5.0)
        assert fake.closed is True

    async def test_reconnects_with_backoff_after_failure(self):
        attempts = {"n": 0}
        received = []
        disconnects = []

        async def _connect(url, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ConnectionError("first attempt fails")
            return FakeWS(messages=["quote"])

        # reset_on_success=False：失败计数持续累积，max_retries=1 可终止循环
        policy = ReconnectPolicy(
            initial_backoff=0.01, max_backoff=0.05, max_retries=1, reset_on_success=False
        )
        channel = WebSocketChannel(
            watched_codes=["600519"],
            on_message=received.append,
            on_disconnect=lambda: disconnects.append("dc"),
            reconnect_policy=policy,
        )
        channel._url = "wss://example.com/ws"
        with patch("websockets.connect", side_effect=_connect):
            await asyncio.wait_for(channel._run_loop(), timeout=5.0)

        assert attempts["n"] == 2
        assert received == ["quote"]
        assert disconnects == ["dc", "dc"]
        assert channel.connected is False

    async def test_retry_reset_on_successful_connect(self):
        fake = FakeWS()
        channel = WebSocketChannel(reconnect_policy=ReconnectPolicy(reset_on_success=True))
        channel._retry = 5  # 模拟多次连续失败后的状态
        with patch("websockets.connect", side_effect=_connect_ok(fake)):
            await channel.connect("wss://example.com/ws")
        assert channel._retry == 0

    async def test_retry_not_reset_when_disabled(self):
        fake = FakeWS()
        channel = WebSocketChannel(reconnect_policy=ReconnectPolicy(reset_on_success=False))
        channel._retry = 5
        with patch("websockets.connect", side_effect=_connect_ok(fake)):
            await channel.connect("wss://example.com/ws")
        assert channel._retry == 5

    async def test_receive_exception_triggers_disconnect_and_retry(self):
        disconnects = []
        fake = FakeWS(fail_iter=ConnectionError("peer reset"))
        channel = WebSocketChannel(
            on_disconnect=lambda: disconnects.append("dc"),
            reconnect_policy=ReconnectPolicy(max_retries=0),
        )
        channel._url = "wss://example.com/ws"
        with self.assertLogs("paper_trading.ws_channel", level="WARNING"):
            with patch("websockets.connect", side_effect=_connect_ok(fake)):
                await asyncio.wait_for(channel._run_loop(), timeout=5.0)
        assert fake.closed is True
        assert disconnects == ["dc"]

    async def test_stop_while_connected_exits_without_disconnect_callback(self):
        disconnects = []
        blocking = FakeWS(block=True)
        channel = WebSocketChannel(on_disconnect=lambda: disconnects.append("dc"))
        channel._url = "wss://example.com/ws"

        async def _stop():
            await asyncio.sleep(0.02)
            channel.stop()

        with patch("websockets.connect", side_effect=_connect_ok(blocking)):
            await asyncio.wait_for(asyncio.gather(channel._run_loop(), _stop()), timeout=5.0)
        assert blocking.closed is True
        assert disconnects == []

    async def test_stop_wakes_backoff_sleep(self):
        channel = WebSocketChannel(reconnect_policy=ReconnectPolicy())  # 默认退避 1s
        channel._url = "wss://example.com/ws"

        async def _stop():
            await asyncio.sleep(0.05)
            channel.stop()

        with patch("websockets.connect", side_effect=_connect_sequence(ConnectionError("down"))):
            start = time.monotonic()
            await asyncio.wait_for(asyncio.gather(channel._run_loop(), _stop()), timeout=5.0)
            elapsed = time.monotonic() - start
        assert elapsed < 0.9  # 远小于默认 1s 退避，证明 stop 唤醒了退避等待

    async def test_resubscribes_after_reconnect(self):
        sent_per_ws = []

        async def _connect(url, **kwargs):
            fake = FakeWS()
            sent_per_ws.append(fake)
            if len(sent_per_ws) == 1:
                fake._fail_iter = ConnectionError("drop after first connect")
            return fake

        channel = WebSocketChannel(
            watched_codes=["600519"],
            reconnect_policy=ReconnectPolicy(initial_backoff=0.01),
        )
        channel._url = "wss://example.com/ws"

        async def _stop():
            await asyncio.sleep(0.03)
            print(f"[dbg-stop] stop_event_before={channel._stop_event.is_set()} shutdown={channel._shutdown.is_set()}", flush=True)
            channel.stop()

        print(f"[dbg-start] stop_event={channel._stop_event.is_set()} shutdown={channel._shutdown.is_set()} url={channel._url}", flush=True)
        with patch("websockets.connect", side_effect=_connect):
            await asyncio.wait_for(asyncio.gather(channel._run_loop(), _stop()), timeout=5.0)
        print(f"[dbg-end] fakes={len(sent_per_ws)} stop_event={channel._stop_event.is_set()} shutdown={channel._shutdown.is_set()}", flush=True)

        # 每次连接都应自动发送订阅消息（含重连后的补订阅）
        assert len(sent_per_ws) >= 2
        for fake in sent_per_ws:
            assert len(fake.sent) == 1
            payload = json.loads(fake.sent[0])
            assert payload["codes"] == ["600519"]


# ----------------------------------------------------------------------
# WebSocketChannel：run_forever（阻塞式，独立线程）
# ----------------------------------------------------------------------

class TestWebSocketChannelRunForever:
    def test_run_forever_without_url_raises(self):
        channel = WebSocketChannel()
        with pytest.raises(RuntimeError):
            channel.run_forever()

    def test_stop_before_run_forever_then_restart(self):
        attempts = {"n": 0}

        async def _connect(url, **kwargs):
            attempts["n"] += 1
            raise ConnectionError("down")

        channel = WebSocketChannel(reconnect_policy=ReconnectPolicy(max_retries=0))
        channel.stop()  # 先 stop，随后 run_forever 应重置状态仍能启动
        with patch("websockets.connect", side_effect=_connect):
            channel.run_forever("wss://example.com/ws")
        assert attempts["n"] == 1

    def test_run_forever_in_thread_stop_from_main(self):
        attempts = {"n": 0}
        blocking = FakeWS(block=True)

        async def _connect(url, **kwargs):
            attempts["n"] += 1
            return blocking

        channel = WebSocketChannel()
        with patch("websockets.connect", side_effect=_connect):
            thread = threading.Thread(
                target=channel.run_forever, args=("wss://example.com/ws",), daemon=True
            )
            thread.start()
            deadline = time.monotonic() + 5.0
            while attempts["n"] == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            assert attempts["n"] >= 1
            channel.stop()
            thread.join(timeout=5.0)
        assert not thread.is_alive()
        assert blocking.closed is True
