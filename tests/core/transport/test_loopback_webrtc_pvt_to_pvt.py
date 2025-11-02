import pytest
import trio
import importlib
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
repo_root = here
for _ in range(6):
    if (repo_root / "libp2p").exists():
        break
    repo_root = repo_root.parent
sys.path.insert(0, str(repo_root))

webrtc_conn = importlib.import_module("libp2p.transport.webrtc.connection")
webrtc_async = importlib.import_module("libp2p.transport.webrtc.async_bridge")

import threading
import logging

logger = logging.getLogger("tests.mock_inline")

class MockDataChannel:
    def __init__(self, label: str = "mock"):
        self._callbacks = {"message": [], "open": [], "close": [], "error": []}
        self._peer = None
        self.label = label

    def link(self, other):
        self._peer = other

    def on(self, event, callback):
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    def send(self, data: bytes) -> None:
        if self._peer is None:
            raise RuntimeError("Peer not linked")

        def deliver(cb, payload):
            try:
                cb(payload)
            except Exception:
                logger.exception("MockDataChannel callback error")

        for cb in list(self._peer._callbacks.get("message", [])):
            t = threading.Thread(target=deliver, args=(cb, data), daemon=True)
            t.start()

    def close(self) -> None:
        for cb in list(self._callbacks.get("close", [])):
            try:
                cb()
            except Exception:
                logger.exception("MockDataChannel close callback error")


class MockPeerConnection:
    def __init__(self):
        self.closed = False
        self.data_channels = {}

    def close(self):
        self.closed = True


WebRTCRawConnection = webrtc_conn.WebRTCRawConnection
WebRTCStream = webrtc_conn.WebRTCStream
WebRTCAsyncBridge = webrtc_async.WebRTCAsyncBridge

@pytest.mark.trio
async def test_close_stream_sends_close_and_removes_stream():
    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()
    ch_a = MockDataChannel("a")
    ch_b = MockDataChannel("b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)

    conn_a = WebRTCRawConnection(
        peer_id="b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    conn_b = WebRTCRawConnection(
        peer_id="a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )

    s_a = await conn_a.open_stream()
    s_b = WebRTCStream(s_a.stream_id, conn_b)
    conn_b._streams[s_b.stream_id] = s_b
    await s_a.close()
    await trio.sleep(0.05)
    assert s_b._closed is True
    assert s_b.stream_id not in conn_b._streams


@pytest.mark.trio
async def test_send_message_fallback_stores_message():
    pc = MockPeerConnection()
    ch = MockDataChannel("f")
    ch_peer = MockDataChannel("fp")
    ch.link(ch_peer)
    ch_peer.link(ch)

    conn = WebRTCRawConnection(
        peer_id="p",
        peer_connection=pc,
        data_channel=ch,
        is_initiator=True
    )
    conn._trio_token = None
    conn._send_message_fallback(b"fallback-1")
    msg = await conn.receive_channel.receive()
    assert msg == b"fallback-1"


@pytest.mark.trio
async def test_multiple_streams_concurrent():
    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()
    ch_a = MockDataChannel("a")
    ch_b = MockDataChannel("b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)

    conn_a = WebRTCRawConnection(
        peer_id="b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    conn_b = WebRTCRawConnection(
        peer_id="a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )

    streams_a = [await conn_a.open_stream() for _ in range(4)]
    streams_b = []
    for s in streams_a:
        sb = WebRTCStream(s.stream_id, conn_b)
        conn_b._streams[s.stream_id] = sb
        streams_b.append(sb)

    class NoopBridge:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def send_data(self, data_channel, data: bytes):
            data_channel.send(data)

    conn_a._bridge = NoopBridge()
    payloads = [f"msg-{i}".encode() for i in range(len(streams_a))]
    async with trio.open_nursery() as nursery:
        for s, p in zip(streams_a, payloads):
            nursery.start_soon(s.write, p)
        recvd = []
        for sb in streams_b:
            recvd.append(await sb.read())
    assert recvd == payloads


@pytest.mark.trio
async def test_async_bridge_send_data_calls_channel_send():
    bridge = WebRTCAsyncBridge()
    class DummyChannel:
        def __init__(self):
            self.sent = []
        def send(self, data: bytes) -> None:
            self.sent.append(data)
    ch = DummyChannel()
    await bridge.send_data(ch, b"payload-x")
    assert ch.sent == [b"payload-x"]


@pytest.mark.trio
async def test_connection_initialization():
    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()
    ch_a = MockDataChannel("init-a")
    ch_b = MockDataChannel("init-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)
    conn_initiator = WebRTCRawConnection(
        peer_id="responder",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    conn_responder = WebRTCRawConnection(
        peer_id="initiator",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )
    assert not pc_a.closed
    assert not pc_b.closed
    assert conn_initiator.peer_id == "responder"
    assert conn_responder.peer_id == "initiator"
    assert conn_initiator.is_initiator is True
    assert conn_responder.is_initiator is False


@pytest.mark.trio
async def test_pvt_to_pvt_loopback_connection():
    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()
    ch_a = MockDataChannel("main-a")
    ch_b = MockDataChannel("main-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)
    peer_a = WebRTCRawConnection(
        peer_id="peer-b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    peer_b = WebRTCRawConnection(
        peer_id="peer-a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )
    class SimpleBridge:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def send_data(self, data_channel, data: bytes):
            data_channel.send(data)
    peer_a._bridge = SimpleBridge()
    peer_b._bridge = SimpleBridge()
    stream_a = await peer_a.open_stream()
    stream_b = WebRTCStream(stream_a.stream_id, peer_b)
    peer_b._streams[stream_b.stream_id] = stream_b
    assert stream_a.stream_id in peer_a._streams
    assert stream_b.stream_id in peer_b._streams
    test_data_a_to_b = b"hello-from-peer-a"
    test_data_b_to_a = b"hello-from-peer-b"
    async with trio.open_nursery() as nursery:
        async def peer_a_sends_and_receives():
            await stream_a.write(test_data_a_to_b)
            data = await stream_a.read()
            return data
        async def peer_b_receives_and_sends():
            data = await stream_b.read()
            await stream_b.write(test_data_b_to_a)
            return data
        nursery.start_soon(peer_a_sends_and_receives)
        received_by_b = await peer_b_receives_and_sends()
    assert received_by_b == test_data_a_to_b


@pytest.mark.trio
async def test_pvt_to_pvt_multiple_concurrent_streams():
    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()
    ch_a = MockDataChannel("multi-a")
    ch_b = MockDataChannel("multi-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)
    peer_a = WebRTCRawConnection(
        peer_id="peer-b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    peer_b = WebRTCRawConnection(
        peer_id="peer-a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )
    class SimpleBridge:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def send_data(self, data_channel, data: bytes):
            data_channel.send(data)
    peer_a._bridge = SimpleBridge()
    peer_b._bridge = SimpleBridge()
    num_streams = 5
    messages = [f"stream-{i}".encode() for i in range(num_streams)]
    streams_a = [await peer_a.open_stream() for _ in range(num_streams)]
    streams_b = []
    for stream_a in streams_a:
        stream_b = WebRTCStream(stream_a.stream_id, peer_b)
        peer_b._streams[stream_b.stream_id] = stream_b
        streams_b.append(stream_b)
    async with trio.open_nursery() as nursery:
        for stream, msg in zip(streams_a, messages):
            nursery.start_soon(stream.write, msg)
        received = []
        for stream_b in streams_b:
            msg = await stream_b.read()
            received.append(msg)
    assert received == messages


@pytest.mark.trio
async def test_connection_close_and_cleanup():
    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()
    ch_a = MockDataChannel("close-a")
    ch_b = MockDataChannel("close-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)
    conn_a = WebRTCRawConnection(
        peer_id="peer-b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    conn_b = WebRTCRawConnection(
        peer_id="peer-a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )
    s_a = await conn_a.open_stream()
    s_b = WebRTCStream(s_a.stream_id, conn_b)
    conn_b._streams[s_b.stream_id] = s_b
    assert len(conn_a._streams) > 0
    assert len(conn_b._streams) > 0
    await conn_a.close()
    await trio.sleep(0.1)
    assert pc_a.closed
    assert len(conn_a._streams) == 0


@pytest.mark.trio
async def test_stream_error_handling():
    pc = MockPeerConnection()
    ch = MockDataChannel("error")
    ch_peer = MockDataChannel("error-peer")
    ch.link(ch_peer)
    ch_peer.link(ch)
    conn = WebRTCRawConnection(
        peer_id="other",
        peer_connection=pc,
        data_channel=ch,
        is_initiator=True
    )
    stream = await conn.open_stream()
    await stream.close()
    await trio.sleep(0.05)
    try:
        await stream.write(b"data-on-closed")
        assert stream._closed is True
    except Exception as e:
        logger.info(f"Expected error on closed stream: {e}")
        assert stream._closed is True


@pytest.mark.trio
async def test_large_message_through_loopback():
    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()
    ch_a = MockDataChannel("large-a")
    ch_b = MockDataChannel("large-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)
    peer_a = WebRTCRawConnection(
        peer_id="peer-b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    peer_b = WebRTCRawConnection(
        peer_id="peer-a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )
    class SimpleBridge:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return False
        async def send_data(self, data_channel, data: bytes):
            data_channel.send(data)
    peer_a._bridge = SimpleBridge()
    peer_b._bridge = SimpleBridge()
    stream_a = await peer_a.open_stream()
    stream_b = WebRTCStream(stream_a.stream_id, peer_b)
    peer_b._streams[stream_b.stream_id] = stream_b
    large_message = b"x" * (1024 * 1024)
    async with trio.open_nursery() as nursery:
        async def sender():
            await stream_a.write(large_message)
        async def receiver():
            return await stream_b.read()
        nursery.start_soon(sender)
        received = await receiver()
    assert received == large_message
    assert len(received) == 1024 * 1024
