"""
Data channel manager for one-data-channel-per-stream architecture.

This module manages the creation and lifecycle of dedicated data channels
for each stream, following the go-libp2p spec-compliant design.

Reference: https://github.com/libp2p/go-libp2p/blob/master/p2p/transport/webrtc/connection.go
"""

import asyncio
import logging

from aiortc import RTCPeerConnection

from .stream import WebRTCStream

logger = logging.getLogger(__name__)


class StreamDataChannelManager:
    """Manages one data channel per stream."""

    def __init__(self, peer_connection: RTCPeerConnection):
        """
        Initialize the data channel manager.

        Args:
            peer_connection: The underlying RTCPeerConnection

        """
        self.peer_connection = peer_connection
        self._streams: dict[int, WebRTCStream] = {}
        self._next_stream_id = 0
        self._stream_id_lock = asyncio.Lock()
        self._closed = False

        # Event handlers
        self.peer_connection.on("datachannel", self._on_data_channel)

    async def _on_data_channel(self, channel) -> None:
        """
        Handle incoming data channel from remote peer.

        Each data channel corresponds to one stream.
        """
        try:
            # Extract stream ID from channel label
            # Format: "libp2p-webrtc-<stream_id>"
            label = channel.label
            if not label.startswith("libp2p-webrtc-"):
                logger.warning(f"Unexpected data channel label: {label}")
                channel.close()
                return

            try:
                stream_id = int(label.split("-")[-1])
            except (IndexError, ValueError):
                logger.warning(f"Could not parse stream ID from label: {label}")
                channel.close()
                return

            # Create stream wrapper
            stream = WebRTCStream(
                stream_id=stream_id,
                data_channel=channel,
                is_initiator=False,
                on_done=lambda: self._on_stream_done(stream_id),
            )

            self._streams[stream_id] = stream

            logger.debug(f"Created stream {stream_id} from incoming data channel")

            # Set up event handlers
            channel.on("message", lambda msg: self._on_message(stream_id, msg))
            channel.on("close", lambda: self._on_channel_close(stream_id))

        except Exception as e:
            logger.error(f"Error handling incoming data channel: {e}", exc_info=True)

    async def create_stream(self) -> WebRTCStream:
        """
        Create a new stream with a dedicated data channel.

        Returns:
            A new WebRTCStream

        """
        if self._closed:
            raise RuntimeError("Connection is closed")

        # Allocate next stream ID
        async with self._stream_id_lock:
            stream_id = self._next_stream_id
            self._next_stream_id += 2  # Increment by 2 for initiator/responder pairs

        # Create data channel
        label = f"libp2p-webrtc-{stream_id}"
        try:
            data_channel = self.peer_connection.createDataChannel(
                label,
                ordered=True,  # Messages must be delivered in order
            )
        except Exception as e:
            logger.error(f"Failed to create data channel for stream {stream_id}: {e}")
            raise

        # Create stream wrapper
        stream = WebRTCStream(
            stream_id=stream_id,
            data_channel=data_channel,
            is_initiator=True,
            on_done=lambda: self._on_stream_done(stream_id),
        )

        self._streams[stream_id] = stream

        # Set up event handlers
        data_channel.on("open", lambda: self._on_channel_open(stream_id))
        data_channel.on("message", lambda msg: self._on_message(stream_id, msg))
        data_channel.on("close", lambda: self._on_channel_close(stream_id))

        logger.debug(f"Created stream {stream_id} with new data channel")

        # Wait for channel to open
        await asyncio.sleep(0.1)  # Give channel time to open

        return stream

    async def _on_message(self, stream_id: int, message) -> None:
        """Handle incoming message on a stream's data channel."""
        stream = self._streams.get(stream_id)
        if not stream:
            logger.warning(f"Received message for unknown stream {stream_id}")
            return

        try:
            # Extract data from message
            data = b""
            if hasattr(message, "data"):
                raw = message.data
                if isinstance(raw, bytes):
                    data = raw
                elif hasattr(raw, "tobytes"):
                    data = raw.tobytes()
                else:
                    data = bytes(raw) if raw else b""
            elif isinstance(message, bytes):
                data = message
            else:
                try:
                    data = bytes(message)
                except (TypeError, ValueError):
                    data = str(message).encode()

            if data:
                # TODO: Deliver message to stream's read buffer
                # This requires async queue integration
                await stream._deliver_message_data(data)

        except Exception as e:
            logger.error(f"Error processing message on stream {stream_id}: {e}")

    def _on_channel_open(self, stream_id: int) -> None:
        """Handle data channel opening."""
        logger.debug(f"Data channel opened for stream {stream_id}")

    def _on_channel_close(self, stream_id: int) -> None:
        """Handle data channel closing."""
        logger.debug(f"Data channel closed for stream {stream_id}")
        self._streams.pop(stream_id, None)

    def _on_stream_done(self, stream_id: int) -> None:
        """Handle stream completion."""
        self._streams.pop(stream_id, None)
        logger.debug(f"Stream {stream_id} completed and cleaned up")

    async def close(self) -> None:
        """Close all streams and the connection."""
        self._closed = True

        # Close all streams
        for stream in list(self._streams.values()):
            try:
                await stream.reset()
            except Exception as e:
                logger.debug(f"Error resetting stream: {e}")

        self._streams.clear()

    def get_stream(self, stream_id: int) -> WebRTCStream | None:
        """Get a stream by ID."""
        return self._streams.get(stream_id)

    def get_all_streams(self) -> list[WebRTCStream]:
        """Get all active streams."""
        return list(self._streams.values())
