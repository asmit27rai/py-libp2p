import logging
from typing import Callable, List, Optional, Any, Dict
import trio
from multiaddr import Multiaddr
from aiortc import RTCPeerConnection, RTCDataChannel, RTCConfiguration, RTCIceServer
from libp2p.abc import IListener
from libp2p.custom_types import THandler
from libp2p.transport.webrtc.connection import WebRTCConnection
from libp2p.transport.webrtc.exceptions import WebRTCConnectionError
from libp2p.transport.webrtc.utils import create_webrtc_multiaddr, dict_to_sdp, dict_to_ice_candidate
from libp2p.transport.webrtc.signaling import WebRTCSignalingManager

logger = logging.getLogger(__name__)

class WebRTCListener(IListener):
    """WebRTC listener implementing IListener interface."""
    
    def __init__(self, handler_function: THandler, signaling_manager: Optional[WebRTCSignalingManager] = None):
        self.handler = handler_function
        self.signaling_manager = signaling_manager
        self._listening_addresses: List[Multiaddr] = []
        self._ice_servers = [
            RTCIceServer(urls="stun:stun.l.google.com:19302"),
            RTCIceServer(urls="stun:stun1.l.google.com:19302"),
        ]
        self._rtc_config = RTCConfiguration(iceServers=self._ice_servers)
        self._active = False
        self._connections: Dict[str, RTCPeerConnection] = {}
        self._nursery = None
    
    async def listen(self, maddr: Multiaddr, nursery: trio.Nursery) -> bool:
        """Start listening for WebRTC connections."""
        try:
            logger.debug(f"WebRTC listener starting on {maddr}")
            self._listening_addresses.append(maddr)
            self._active = True
            self._nursery = nursery
            
            if not self.signaling_manager:
                logger.warning("No signaling manager configured - WebRTC listener will not accept connections")
                return False
            
            # Register ourselves to handle incoming connection requests
            self.signaling_manager.register_connection_handler("*", self._handle_signaling_message)
            
            logger.info(f"WebRTC listener started successfully on {maddr}")
            return True
        except Exception as e:
            logger.error(f"Failed to start WebRTC listener on {maddr}: {e}")
            return False
    
    async def _handle_signaling_message(self, message_type: str, message) -> None:
        """Handle incoming signaling messages."""
        try:
            if message_type == "offer":
                await self._handle_incoming_offer(message)
        except Exception as e:
            logger.error(f"Error handling signaling message: {e}")
    
    async def _handle_incoming_offer(self, message) -> None:
        """Handle incoming WebRTC offer."""
        peer_id = message.peer_id
        offer = dict_to_sdp(message.data)
        
        logger.debug(f"Handling incoming WebRTC offer from {peer_id}")
        
        try:
            # Create RTCPeerConnection for this incoming connection
            pc = RTCPeerConnection(configuration=self._rtc_config)
            self._connections[peer_id] = pc
            
            # Set up data channel handler
            data_channel = None
            channel_ready = trio.Event()
            
            def on_datachannel(event):
                nonlocal data_channel
                data_channel = event.channel
                logger.debug(f"Received data channel: {data_channel.label}")
                trio.from_thread.run_sync(channel_ready.set)
            
            pc.addEventListener("datachannel", on_datachannel)
            
            # Set up ICE candidate handling
            def on_ice_candidate(event):
                if event.candidate:
                    # Send ICE candidate via signaling
                    trio.from_thread.run_sync(
                        self.signaling_manager.send_ice_candidate,
                        peer_id,
                        event.candidate
                    )
            
            pc.addEventListener("icecandidate", on_ice_candidate)
            
            # Set remote description (the offer)
            await trio.to_thread.run_sync(self._set_remote_description, pc, offer)
            
            # Create and send answer
            answer = await trio.to_thread.run_sync(self._create_answer, pc)
            await self.signaling_manager.send_answer(peer_id, answer)
            
            # Wait for data channel to be ready
            await channel_ready.wait()
            
            if data_channel:
                # Create WebRTC connection wrapper
                connection = WebRTCConnection(data_channel, is_initiator=False)
                
                # Handle the connection in a new task
                if self._nursery:
                    self._nursery.start_soon(self._handle_connection, connection)
                else:
                    logger.error("No nursery available to handle connection")
            
        except Exception as e:
            logger.error(f"Error handling incoming offer from {peer_id}: {e}")
            if peer_id in self._connections:
                del self._connections[peer_id]
    
    async def _handle_connection(self, connection: WebRTCConnection) -> None:
        """Handle an established WebRTC connection."""
        try:
            await self.handler(connection)
        except Exception as e:
            logger.error(f"Error in connection handler: {e}")
        finally:
            await connection.close()
    
    def _set_remote_description(self, pc: RTCPeerConnection, description) -> None:
        """Set remote description (runs in asyncio context)."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(pc.setRemoteDescription(description))
        finally:
            loop.close()
    
    def _create_answer(self, pc: RTCPeerConnection):
        """Create WebRTC answer (runs in asyncio context)."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _do_create_answer():
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                return answer
            
            return loop.run_until_complete(_do_create_answer())
        finally:
            loop.close()
    
    def get_addrs(self) -> tuple[Multiaddr, ...]:
        """Get listening addresses."""
        return tuple(self._listening_addresses)
    
    async def close(self) -> None:
        """Close the listener."""
        self._active = False
        
        # Close all active connections
        for peer_id, pc in self._connections.items():
            try:
                await trio.to_thread.run_sync(self._close_peer_connection, pc)
            except Exception as e:
                logger.error(f"Error closing connection to {peer_id}: {e}")
        
        self._connections.clear()
        self._listening_addresses.clear()
        
        if self.signaling_manager:
            self.signaling_manager.unregister_connection_handler("*")
        
        logger.debug("WebRTC listener closed")
    
    def _close_peer_connection(self, pc: RTCPeerConnection) -> None:
        """Close a peer connection (runs in asyncio context)."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(pc.close())
        finally:
            loop.close()
