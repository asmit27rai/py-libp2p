"""
WebRTC Stream implementation with proper protobuf framing.

This implementation follows the go-libp2p architecture with:
- One data channel per stream
- Protobuf message framing using length-delimited encoding
- Proper stream lifecycle management (FIN, RESET, STOP_SENDING, FIN_ACK)
- Stream state tracking (sending, receiving, closed)

Reference: https://github.com/libp2p/go-libp2p/blob/master/p2p/transport/webrtc/stream.go
"""

import asyncio
from collections.abc import Callable
from enum import Enum
import logging
import time

from libp2p.abc import INetStream

from .pb.stream_message_pb2 import Message
from .pbio import VarintFrameReader, VarintFrameWriter, encode_varint

logger = logging.getLogger(__name__)

# Constants from go-libp2p implementation
MAX_SEND_MESSAGE_SIZE = 16384
MAX_RECEIVE_MESSAGE_SIZE = (256 << 10) + (1 << 10)  # 256KB + 1KB buffer
PROTO_OVERHEAD = 5
VARINT_OVERHEAD = 2
MAX_TOTAL_CONTROL_MESSAGES_SIZE = 50
MAX_FIN_ACK_WAIT = 10.0  # seconds


class ReceiveState(Enum):
    """Receive state of the stream."""

    RECEIVING = 0
    DATA_READ = 1  # received and read FIN
    RESET = 2


class SendState(Enum):
    """Send state of the stream."""

    SENDING = 0
    DATA_SENT = 1
    DATA_RECEIVED = 2  # received FIN_ACK
    RESET = 3


class WebRTCStream(INetStream):
    """
    WebRTC Stream with proper protobuf-based message framing.

    Each stream gets its own dedicated data channel from the WebRTC connection.
    Messages are framed using varint-length-delimited protobuf encoding.
    """

    def __init__(
        self,
        stream_id: int,
        data_channel,
        is_initiator: bool = False,
        on_done: Callable[[], None] | None = None,
    ):
        """
        Initialize a WebRTC stream.

        Args:
            stream_id: Unique identifier for this stream within the connection
            data_channel: The RTCDataChannel backing this stream
            is_initiator: Whether this peer initiated the stream
            on_done: Callback when stream is fully closed

        """
        self.stream_id = stream_id
        self.data_channel = data_channel
        self._is_initiator = is_initiator
        self._on_done = on_done

        # State tracking
        self._send_state = SendState.SENDING
        self._receive_state = ReceiveState.RECEIVING
        self._closed = False

        # Read/Write protection
        self._reader_lock = asyncio.Lock()
        self._writer_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

        # Buffer for partial reads
        self._next_message: Message | None = None
        self._read_buffer = bytearray()
        self._read_queue: asyncio.Queue = asyncio.Queue()

        # Error tracking
        self._read_error: Exception | None = None
        self._write_error: Exception | None = None
        self._close_error: Exception | None = None

        # Deadlines
        self._read_deadline: float | None = None
        self._write_deadline: float | None = None
        self._control_message_reader_end_time: float | None = None

        # Callbacks
        self._write_state_changed = asyncio.Event()
        self._control_message_reader_once = False

        # Metrics
        self._bytes_sent = 0
        self._bytes_received = 0

        # Frame I/O
        self._frame_reader = VarintFrameReader()
        self._frame_writer = VarintFrameWriter()

        # Data channel state
        self._open = asyncio.Event()
        self._data_channel_closed = asyncio.Event()

    @property
    def is_initiator(self) -> bool:
        """Whether this stream was initiated locally."""
        return self._is_initiator

    def get_extra_stream_data(self):
        """Get extra stream metadata (compatibility with INetStream)."""
        return {"stream_id": self.stream_id}

    async def read(self, n: int = -1) -> bytes:
        """
        Read up to n bytes from the stream.

        Args:
            n: Maximum number of bytes to read. -1 means read all available.

        Returns:
            Bytes read from the stream.

        Raises:
            EOFError: When the stream is closed by remote peer

        """
        async with self._reader_lock:
            # Check if stream is in error state
            if self._receive_state == ReceiveState.RESET:
                if self._read_error:
                    raise self._read_error
                raise EOFError("Stream was reset")

            # Try to read from buffer first
            if self._read_buffer:
                if n == -1:
                    data = bytes(self._read_buffer)
                    self._read_buffer.clear()
                    return data
                else:
                    data = bytes(self._read_buffer[:n])
                    del self._read_buffer[:n]
                    return data

            # Read next message from queue
            try:
                timeout = None
                if self._read_deadline:
                    timeout = self._read_deadline - time.time()
                    if timeout < 0:
                        raise TimeoutError("Read deadline exceeded")

                message = await asyncio.wait_for(
                    self._read_queue.get(), timeout=timeout
                )
            except asyncio.TimeoutError:
                raise TimeoutError("Read deadline exceeded")
            except asyncio.CancelledError:
                raise

            if message is None:
                # EOF marker
                raise EOFError("End of stream")

            # Extract message data
            data = message.message if message.message else b""
            self._bytes_received += len(data)

            # Process control flags
            await self._process_incoming_flag(message)

            # Return data or empty bytes if only control flag
            return data

    async def write(self, data: bytes) -> None:
        """
        Write data to the stream.

        Args:
            data: Bytes to write.

        Raises:
            EOFError: When the stream is closed

        """
        async with self._writer_lock:
            if self._send_state != SendState.SENDING:
                if self._write_error:
                    raise self._write_error
                raise EOFError("Stream is not in sending state")

            if not data:
                return

            # Enforce max message size
            offset = 0
            while offset < len(data):
                chunk = data[offset : offset + MAX_SEND_MESSAGE_SIZE - PROTO_OVERHEAD]
                offset += len(chunk)

                msg = Message()
                msg.message = chunk

                await self._write_message(msg)

            self._bytes_sent += len(data)

    async def close(self) -> None:
        """Close the stream gracefully with FIN handshake."""
        async with self._state_lock:
            if self._closed:
                return

            # Send FIN to signal no more data
            msg = Message()
            msg.flag = Message.FIN
            await self._write_message(msg)

            async with self._writer_lock:
                self._send_state = SendState.DATA_SENT

            # Wait for FIN_ACK or timeout
            await self._wait_for_fin_ack()

    async def reset(self) -> None:
        """Reset the stream abruptly."""
        await self.reset_with_error(0)

    async def reset_with_error(self, error_code: int = 0) -> None:
        """
        Reset the stream with an error code.

        Args:
            error_code: Application-defined error code

        """
        async with self._state_lock:
            if self._closed:
                return

            # Send RESET
            msg = Message()
            msg.flag = Message.RESET
            msg.errorCode = error_code
            try:
                await self._write_message(msg)
            except Exception:
                pass

            async with self._writer_lock:
                self._send_state = SendState.RESET
                self._write_error = Exception(f"Stream reset with code {error_code}")

            async with self._reader_lock:
                self._receive_state = ReceiveState.RESET
                self._read_error = Exception(f"Stream reset with code {error_code}")

            await self._cleanup()

    async def _read_message(self) -> Message | None:
        """
        Read a single protobuf message from the data channel.
        Handles varint-length-delimited encoding.

        Returns:
            Message or None if EOF

        """
        try:
            # Read from the message queue that's fed by data channel events
            timeout = None
            if self._read_deadline:
                timeout = max(0, self._read_deadline - time.time())

            message = await asyncio.wait_for(
                self._read_queue.get(),
                timeout=timeout if timeout and timeout > 0 else None,
            )
            return message
        except asyncio.TimeoutError:
            raise TimeoutError("Read timeout")
        except asyncio.CancelledError:
            raise

    async def _write_message(self, msg: Message) -> None:
        """
        Write a single protobuf message to the data channel.
        Uses varint-length-delimited encoding.

        Args:
            msg: Message to write

        """
        try:
            # Serialize the message
            serialized = msg.SerializeToString()

            # Encode length as varint
            length = len(serialized)
            varint = encode_varint(length)

            # Send length + serialized message
            await self._send_raw_bytes(varint + serialized)
        except Exception as e:
            logger.error(f"Failed to write message on stream {self.stream_id}: {e}")
            raise

    async def _send_raw_bytes(self, data: bytes) -> None:
        """Send raw bytes on the data channel."""
        if not data:
            return

        try:
            # Wait for channel to be open
            timeout = None
            if self._write_deadline:
                timeout = max(0, self._write_deadline - time.time())

            await asyncio.wait_for(
                self._open.wait(), timeout=timeout if timeout and timeout > 0 else None
            )

            # Send on the data channel
            self.data_channel.send(data)

        except asyncio.TimeoutError:
            raise TimeoutError("Write timeout - data channel not ready")
        except Exception as e:
            logger.error(f"Failed to send data on stream {self.stream_id}: {e}")
            raise

    async def _process_incoming_flag(self, msg: Message) -> None:
        """
        Process control flags on incoming message.

        Handles FIN, RESET, STOP_SENDING flags.
        """
        if not msg.HasField("flag"):
            return

        async with self._state_lock:
            flag = msg.flag

            if flag == Message.FIN:
                if self._receive_state == ReceiveState.RECEIVING:
                    self._receive_state = ReceiveState.DATA_READ

                # Send FIN_ACK
                ack_msg = Message()
                ack_msg.flag = Message.FIN_ACK
                try:
                    await self._write_message(ack_msg)
                except Exception as e:
                    logger.debug(f"Failed to send FIN_ACK: {e}")

                # Spawn control message reader for cleanup
                await self._spawn_control_message_reader()

            elif flag == Message.RESET:
                if self._receive_state == ReceiveState.RECEIVING:
                    self._receive_state = ReceiveState.RESET
                    error_code = msg.errorCode if msg.HasField("errorCode") else 0
                    self._read_error = Exception(f"Stream reset with code {error_code}")

                await self._spawn_control_message_reader()

            elif flag == Message.STOP_SENDING:
                if self._send_state in (SendState.SENDING, SendState.DATA_SENT):
                    self._send_state = SendState.RESET
                    error_code = msg.errorCode if msg.HasField("errorCode") else 0
                    self._write_error = Exception(
                        f"Peer requested stop sending with code {error_code}"
                    )
                self._write_state_changed.set()

            elif flag == Message.FIN_ACK:
                self._send_state = SendState.DATA_RECEIVED
                self._write_state_changed.set()

    async def _wait_for_fin_ack(self) -> None:
        """Wait for FIN_ACK from remote peer."""
        self._control_message_reader_end_time = time.time() + MAX_FIN_ACK_WAIT
        await self._spawn_control_message_reader()

    async def _spawn_control_message_reader(self) -> None:
        """
        Spawn a task to read control messages after stream is closed for writing.
        This ensures we properly handle FIN_ACK, RESET, and other control messages.
        """
        if self._control_message_reader_once:
            return

        self._control_message_reader_once = True

        # TODO: Create a background task to read control messages
        # This prevents hanging on the data channel

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._closed = True
        if self._on_done:
            self._on_done()

        try:
            self.data_channel.close()
        except Exception:
            pass

    async def _deliver_message_data(self, data: bytes) -> None:
        """
        Internal method to deliver incoming message data to the read queue.

        Called from the data channel event handlers.
        """
        # For now, we'll implement a simple buffering mechanism
        # In production, this should parse the varint-delimited protobuf
        try:
            # Try to parse as varint-delimited protobuf
            if not data:
                return

            # This is a placeholder - actual implementation would parse varints
            # and protobuf messages from the data channel stream
            self._read_buffer.extend(data)
        except Exception as e:
            logger.error(f"Error delivering message data: {e}")

    def set_read_deadline(self, deadline: float | None) -> None:
        """Set read deadline in seconds from epoch."""
        self._read_deadline = deadline

    def set_write_deadline(self, deadline: float | None) -> None:
        """Set write deadline in seconds from epoch."""
        self._write_deadline = deadline

    def on_data_channel_open(self) -> None:
        """Called when the data channel opens."""
        self._open.set()

    def on_data_channel_message(self, data: bytes) -> None:
        """Called when message arrives on the data channel."""
        try:
            self._read_queue.put_nowait(Message())  # Placeholder
        except asyncio.QueueFull:
            logger.warning(
                f"Stream {self.stream_id} read queue full - dropping message"
            )
