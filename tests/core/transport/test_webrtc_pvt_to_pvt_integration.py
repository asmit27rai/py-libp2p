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

logger = logging.getLogger("tests.mock_inline_integration")


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

    def close(self):
        self.closed = True


WebRTCRawConnection = webrtc_conn.WebRTCRawConnection
WebRTCStream = webrtc_conn.WebRTCStream
WebRTCAsyncBridge = webrtc_async.WebRTCAsyncBridge


class SimpleBridge:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def send_data(self, data_channel, data: bytes):
        data_channel.send(data)


@pytest.mark.trio
async def test_pvt_to_pvt_integration_basic_connection():
    ch_a = MockDataChannel("host-a")
    ch_b = MockDataChannel("host-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)

    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()

    conn_a = WebRTCRawConnection(
        peer_id="host-b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )

    conn_b = WebRTCRawConnection(
        peer_id="host-a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )

    assert not pc_a.closed
    assert not pc_b.closed
    assert conn_a.is_initiator is True
    assert conn_b.is_initiator is False
    print("Phase 1: Connection initialization successful")


@pytest.mark.trio
async def test_pvt_to_pvt_integration_stream_exchange():
    ch_a = MockDataChannel("host-a")
    ch_b = MockDataChannel("host-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)

    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()

    conn_a = WebRTCRawConnection(
        peer_id="host-b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    conn_b = WebRTCRawConnection(
        peer_id="host-a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )

    print("Host A opening stream...")
    stream_a = await conn_a.open_stream()

    print("Host B accepting stream...")
    stream_b = WebRTCStream(stream_a.stream_id, conn_b)
    conn_b._streams[stream_b.stream_id] = stream_b

    assert stream_a.stream_id in conn_a._streams
    assert stream_b.stream_id in conn_b._streams
    print("Phase 2: Streams created successfully")

    conn_a._bridge = SimpleBridge()
    conn_b._bridge = SimpleBridge()

    msg_a_to_b = b"Hello from Host A"
    msg_b_to_a = b"Hello from Host B"

    received_by_b = None
    received_by_a = None

    async with trio.open_nursery() as nursery:
        async def host_a_operation():
            nonlocal received_by_a
            print(f"  → Sending: {msg_a_to_b}")
            await stream_a.write(msg_a_to_b)
            received_by_a = await stream_a.read()
            print(f"  ← Received: {received_by_a}")

        async def host_b_operation():
            nonlocal received_by_b
            received_by_b = await stream_b.read()
            print(f"  ← Received: {received_by_b}")
            print(f"  → Sending: {msg_b_to_a}")
            await stream_b.write(msg_b_to_a)

        nursery.start_soon(host_a_operation)
        nursery.start_soon(host_b_operation)

    assert received_by_b == msg_a_to_b
    assert received_by_a == msg_b_to_a
    print("Phase 4: Bidirectional data exchange successful")

    await stream_a.close()
    await trio.sleep(0.05)

    assert stream_b._closed is True
    assert stream_b.stream_id not in conn_b._streams
    print("Phase 5: Cleanup successful")


@pytest.mark.trio
async def test_pvt_to_pvt_integration_multi_stream():
    ch_a = MockDataChannel("host-a")
    ch_b = MockDataChannel("host-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)

    conn_a = WebRTCRawConnection(
        peer_id="host-b",
        peer_connection=MockPeerConnection(),
        data_channel=ch_a,
        is_initiator=True
    )
    conn_b = WebRTCRawConnection(
        peer_id="host-a",
        peer_connection=MockPeerConnection(),
        data_channel=ch_b,
        is_initiator=False
    )

    num_streams = 3
    print(f"Opening {num_streams} concurrent streams...")
    streams_a = [await conn_a.open_stream() for _ in range(num_streams)]

    streams_b = []
    for s_a in streams_a:
        s_b = WebRTCStream(s_a.stream_id, conn_b)
        conn_b._streams[s_b.stream_id] = s_b
        streams_b.append(s_b)

    assert len(conn_a._streams) >= num_streams
    assert len(conn_b._streams) >= num_streams
    print(f"All {num_streams} streams established")

    conn_a._bridge = SimpleBridge()

    messages = [f"stream-msg-{i}".encode() for i in range(num_streams)]
    print(f"Sending {num_streams} concurrent messages...")

    async with trio.open_nursery() as nursery:
        for stream, msg in zip(streams_a, messages):
            print(f"  → {msg}")
            nursery.start_soon(stream.write, msg)

        received = []
        for stream_b in streams_b:
            msg = await stream_b.read()
            received.append(msg)
            print(f"  ← {msg}")

    assert received == messages
    print(f"All {num_streams} messages received correctly")


@pytest.mark.trio
async def test_pvt_to_pvt_integration_connection_lifecycle():
    ch_a = MockDataChannel("host-a")
    ch_b = MockDataChannel("host-b")
    ch_a.link(ch_b)
    ch_b.link(ch_a)

    pc_a = MockPeerConnection()
    pc_b = MockPeerConnection()

    conn_a = WebRTCRawConnection(
        peer_id="host-b",
        peer_connection=pc_a,
        data_channel=ch_a,
        is_initiator=True
    )
    conn_b = WebRTCRawConnection(
        peer_id="host-a",
        peer_connection=pc_b,
        data_channel=ch_b,
        is_initiator=False
    )

    print("Connections initialized")
    assert not pc_a.closed
    assert not pc_b.closed

    stream_a = await conn_a.open_stream()
    stream_b = WebRTCStream(stream_a.stream_id, conn_b)
    conn_b._streams[stream_b.stream_id] = stream_b

    initial_stream_count_a = len(conn_a._streams)
    initial_stream_count_b = len(conn_b._streams)
    print(f"Streams created: A has {initial_stream_count_a}, B has {initial_stream_count_b}")

    conn_a._bridge = SimpleBridge()
    conn_b._bridge = SimpleBridge()

    async with trio.open_nursery() as nursery:
        async def exchange():
            await stream_a.write(b"test")
            await stream_b.read()
        nursery.start_soon(exchange)

    print("Data exchanged successfully")

    await stream_a.close()
    await trio.sleep(0.05)
    assert stream_b._closed is True
    print("Streams closed")

    pc_a.close()
    pc_b.close()
    assert pc_a.closed
    assert pc_b.closed
    print("Connections closed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
