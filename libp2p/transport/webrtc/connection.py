# libp2p/transport/webrtc/connection.py
import asyncio
import logging
from typing import Optional, Any
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
            asyncio.Queue
        )
        
        # Set up event handlers
        self.data_channel.on("message", self._on_message)
        self.data_channel.on("close", self._on_close)
    
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
    
    def _on_close(self) -> None:
        """Handle channel close."""
        self._closed = True
        trio.from_thread.run_sync(self._signal_close)
    
    async def _signal_close(self) -> None:
        """Signal that channel is closed."""
        await self._receive_queue.put(None)  # EOF marker

class WebRTCConnection(IRawConnection):
    """WebRTC connection implementing IRawConnection interface."""
    
    def __init__(self, data_channel: RTCDataChannel, is_initiator: bool):
        self.data_channel_wrapper = WebRTCDataChannelWrapper(data_channel)
        self.is_initiator = is_initiator
        self._closed = False
    
    async def write(self, data: bytes) -> int:
        """Write data to the WebRTC data channel."""
        if self._closed:
            raise WebRTCDataChannelError("Connection is closed")
        
        try:
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
            message = await self.data_channel_wrapper._receive_queue.get()
            if message is None:  # EOF marker
                self._closed = True
                return b""
            
            if n == -1 or len(message) <= n:
                return message
            else:
                # If we need to return partial data, put remainder back
                remainder = message[n:]
                await self.data_channel_wrapper._receive_queue.put(remainder)
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
