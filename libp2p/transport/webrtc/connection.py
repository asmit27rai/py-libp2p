# libp2p/transport/webrtc/connection.py
import asyncio
import logging
from typing import Optional, Any, Union
import trio
from aiortc import RTCDataChannel
from libp2p.abc import IRawConnection
from libp2p.transport.webrtc.exceptions import WebRTCDataChannelError

logger = logging.getLogger(__name__)

class WebRTCDataChannelWrapper:
    """Wrapper to make aiortc DataChannel compatible with trio."""
    
    def __init__(self, data_channel: RTCDataChannel):
        self.data_channel = data_channel
        self._closed = False
        self._receive_queue = trio.lowlevel.current_trio_token().run_sync_in_context_of(
            lambda: asyncio.Queue()
        )
        self._connection_ready = trio.Event()
        
        # Set up event handlers
        self.data_channel.on("open", self._on_open)
        self.data_channel.on("message", self._on_message)
        self.data_channel.on("close", self._on_close)
        self.data_channel.on("error", self._on_error)
    
    def _on_message(self, message: Any) -> None:
        """Handle incoming messages."""
        try:
            if isinstance(message, str):
                data = message.encode('utf-8')
            elif isinstance(message, bytes):
                data = message
            else:
                data = str(message).encode('utf-8')
            
            # Put message in queue (thread-safe)
            trio.from_thread.run_sync(self._put_message, data)
        except Exception as e:
            logger.error(f"Error handling WebRTC message: {e}")
    
    async def _put_message(self, data: bytes) -> None:
        """Put message in receive queue."""
        await self._receive_queue.put(data)
    
    def _on_open(self) -> None:
        """Handle data channel open."""
        logger.debug("WebRTC data channel opened")
        trio.from_thread.run_sync(self._connection_ready.set)
    
    def _on_close(self) -> None:
        """Handle channel close."""
        logger.debug("WebRTC data channel closed")
        self._closed = True
        trio.from_thread.run_sync(self._signal_close)
    
    def _on_error(self, error: Exception) -> None:
        """Handle channel error."""
        logger.error(f"WebRTC data channel error: {error}")
        self._closed = True
        trio.from_thread.run_sync(self._signal_close)
    
    async def _signal_close(self) -> None:
        """Signal that channel is closed."""
        await self._receive_queue.put(None)  # EOF marker
    
    async def wait_for_open(self) -> None:
        """Wait for the data channel to be ready."""
        if self.data_channel.readyState == "open":
            return
        await self._connection_ready.wait()

class WebRTCConnection(IRawConnection):
    """WebRTC connection implementing IRawConnection interface."""
    
    def __init__(self, data_channel: RTCDataChannel, is_initiator: bool):
        self.data_channel_wrapper = WebRTCDataChannelWrapper(data_channel)
        self.is_initiator = is_initiator
        self._closed = False
        self._buffer = b""
    
    async def write(self, data: bytes) -> int:
        """Write data to the WebRTC data channel."""
        if self._closed:
            raise WebRTCDataChannelError("Connection is closed")
        
        try:
            # Ensure the channel is ready
            await self.data_channel_wrapper.wait_for_open()
            
            # Convert trio context to asyncio for aiortc
            await trio.to_thread.run_sync(self._async_send, data)
            return len(data)
        except Exception as e:
            logger.error(f"Error writing to WebRTC connection: {e}")
            raise WebRTCDataChannelError(f"Failed to write data: {e}") from e
    
    def _async_send(self, data: bytes) -> None:
        """Send data using aiortc (runs in asyncio context)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.data_channel_wrapper.data_channel.send(data))
        finally:
            loop.close()
    
    async def read(self, n: int = -1) -> bytes:
        """Read data from the WebRTC data channel."""
        if self._closed:
            return b""
        
        try:
            # If we have buffered data, use it first
            if self._buffer:
                if n == -1 or len(self._buffer) <= n:
                    result = self._buffer
                    self._buffer = b""
                    return result
                else:
                    result = self._buffer[:n]
                    self._buffer = self._buffer[n:]
                    return result
            
            # Read new data from the queue
            message = await self.data_channel_wrapper._receive_queue.get()
            if message is None:  # EOF marker
                self._closed = True
                return b""
            
            if n == -1 or len(message) <= n:
                return message
            else:
                # Buffer the remainder for next read
                self._buffer = message[n:]
                return message[:n]
        except Exception as e:
            logger.error(f"Error reading from WebRTC connection: {e}")
            raise WebRTCDataChannelError(f"Failed to read data: {e}") from e
    
    async def close(self) -> None:
        """Close the WebRTC connection."""
        if not self._closed:
            self._closed = True
            try:
                await trio.to_thread.run_sync(self._async_close)
            except Exception as e:
                logger.error(f"Error closing WebRTC connection: {e}")
                logger.error(f"Error closing WebRTC connection: {e}")
    
    def _async_close(self) -> None:
        """Close the data channel (runs in asyncio context)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.data_channel_wrapper.data_channel.close())
        finally:
            loop.close()
    
    @property
    def is_closed(self) -> bool:
        """Check if connection is closed."""
        return self._closed
