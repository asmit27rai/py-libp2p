"""
Protocol buffer I/O utilities for varint-length-delimited message encoding.

This module provides utilities for reading and writing protobuf messages
with proper varint-length-delimited framing, as used in the libp2p WebRTC spec.

Reference:
- https://developers.google.com/protocol-buffers/docs/techniques#large-data
- go-libp2p msgio: https://github.com/libp2p/go-msgio
"""

import asyncio

from .pb.stream_message_pb2 import Message


class VarintFrameReader:
    """Reader for varint-length-delimited protobuf messages."""

    def __init__(self, max_message_size: int = 256 * 1024 + 1024):
        """
        Initialize the reader.

        Args:
            max_message_size: Maximum allowed message size (default 257KB)

        """
        self.max_message_size = max_message_size
        self._buffer = bytearray()

    async def read_message(
        self,
        read_func,
        timeout: float | None = None,
    ) -> Message | None:
        """
        Read a single varint-length-delimited protobuf message.

        Args:
            read_func: Async function that reads bytes from the data channel
            timeout: Optional read timeout in seconds

        Returns:
            Deserialized Message or None if EOF

        Raises:
            ValueError: If message is too large
            asyncio.TimeoutError: If read times out

        """
        try:
            # Read varint length
            length = await self._read_varint(read_func, timeout)

            if length is None:
                return None  # EOF

            if length > self.max_message_size:
                raise ValueError(
                    f"Message size {length} exceeds max {self.max_message_size}"
                )

            # Read message bytes
            message_bytes = await self._read_exact(read_func, length, timeout)

            if len(message_bytes) != length:
                raise ValueError(f"Expected {length} bytes, got {len(message_bytes)}")

            # Deserialize
            msg = Message()
            msg.ParseFromString(message_bytes)
            return msg

        except asyncio.TimeoutError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to read message: {e}") from e

    async def _read_varint(
        self,
        read_func,
        timeout: float | None = None,
    ) -> int | None:
        """
        Read a varint from the data stream.

        Returns:
            The decoded integer, or None if EOF

        """
        result = 0
        shift = 0

        while True:
            try:
                data = await asyncio.wait_for(read_func(1), timeout)
            except asyncio.TimeoutError:
                raise

            if not data:
                return None  # EOF

            byte = data[0]
            result |= (byte & 0x7F) << shift

            if not (byte & 0x80):
                return result

            shift += 7

            if shift >= 64:
                raise ValueError("Varint too large")

    async def _read_exact(
        self,
        read_func,
        n: int,
        timeout: float | None = None,
    ) -> bytes:
        """
        Read exactly n bytes.

        Args:
            read_func: Async function that reads bytes
            n: Number of bytes to read
            timeout: Optional timeout

        Returns:
            Exactly n bytes

        """
        result = bytearray()

        while len(result) < n:
            try:
                data = await asyncio.wait_for(read_func(n - len(result)), timeout)
            except asyncio.TimeoutError:
                raise

            if not data:
                raise ValueError(f"EOF while reading {n} bytes")

            result.extend(data)

        return bytes(result)


class VarintFrameWriter:
    """Writer for varint-length-delimited protobuf messages."""

    def __init__(self):
        """Initialize the writer."""
        pass

    async def write_message(
        self,
        msg: Message,
        write_func,
        timeout: float | None = None,
    ) -> None:
        """
        Write a varint-length-delimited protobuf message.

        Args:
            msg: Message to write
            write_func: Async function that writes bytes to the data channel
            timeout: Optional write timeout in seconds

        Raises:
            asyncio.TimeoutError: If write times out

        """
        # Serialize message
        message_bytes = msg.SerializeToString()

        # Encode varint length
        length_bytes = encode_varint(len(message_bytes))

        # Write length + message
        data = length_bytes + message_bytes

        try:
            await asyncio.wait_for(write_func(data), timeout)
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to write message: {e}") from e


def encode_varint(n: int) -> bytes:
    """
    Encode an integer as a varint (variable-length integer).

    Args:
        n: Integer to encode

    Returns:
        Encoded varint bytes

    """
    if n < 0:
        raise ValueError("Cannot encode negative integers as varint")

    result = bytearray()

    while n > 0x7F:
        result.append((n & 0x7F) | 0x80)
        n >>= 7

    result.append(n & 0x7F)
    return bytes(result)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """
    Decode a varint from bytes.

    Args:
        data: Bytes containing the varint
        offset: Starting offset in the data

    Returns:
        Tuple of (decoded_value, bytes_consumed)

    Raises:
        ValueError: If the varint is invalid

    """
    result = 0
    shift = 0
    i = offset

    while i < len(data):
        byte = data[i]
        result |= (byte & 0x7F) << shift

        if not (byte & 0x80):
            return result, i - offset + 1

        shift += 7

        if shift >= 64:
            raise ValueError("Varint too large")

        i += 1

    raise ValueError("Incomplete varint")
