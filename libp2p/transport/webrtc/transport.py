import asyncio
import json
import logging
from typing import Optional, Dict, Any
import trio
from multiaddr import Multiaddr
from aiortc import RTCPeerConnection, RTCDataChannel, RTCConfiguration, RTCIceServer, RTCSessionDescription
from libp2p.abc import ITransport, IRawConnection, IListener
from libp2p.custom_types import THandler
from libp2p.transport.webrtc.listener import WebRTCListener
from libp2p.transport.webrtc.connection import WebRTCConnection
from libp2p.transport.webrtc.exceptions import WebRTCConnectionError, WebRTCSignalingError
from libp2p.transport.webrtc.signaling import (
    WebRTCSignalingManager,
    WebSocketSignalingChannel,
    CircuitRelaySignalingChannel
)
from libp2p.transport.webrtc.utils import (
    is_valid_webrtc_multiaddr,
    ice_candidate_to_dict,
    dict_to_ice_candidate,
    sdp_to_dict,
    dict_to_sdp
)

logger = logging.getLogger(__name__)

class WebRTCTransport(ITransport):
    """WebRTC transport implementing ITransport interface."""
    
    def __init__(self, signaling_manager: Optional[WebRTCSignalingManager] = None):
        self._ice_servers = [
            RTCIceServer(urls="stun:stun.l.google.com:19302"),
            RTCIceServer(urls="stun:stun1.l.google.com:19302"),
        ]
        self._rtc_config = RTCConfiguration(iceServers=self._ice_servers)
        self.signaling_manager = signaling_manager
        self._connections: Dict[str, RTCPeerConnection] = {}
    
    async def dial(self, maddr: Multiaddr) -> IRawConnection:
        """Dial a peer using WebRTC."""
        logger.debug(f"Dialing WebRTC peer at {maddr}")
        
        if not is_valid_webrtc_multiaddr(maddr):
            raise WebRTCConnectionError(f"Invalid WebRTC multiaddr: {maddr}")
        
        if not self.signaling_manager:
            raise WebRTCSignalingError("No signaling manager configured")
        
        try:
            # Extract peer ID from multiaddr
            peer_id = self._extract_peer_id(maddr)
            if not peer_id:
                raise WebRTCConnectionError("No peer ID found in multiaddr")
            
            # Create RTCPeerConnection
            pc = RTCPeerConnection(configuration=self._rtc_config)
            self._connections[peer_id] = pc
            
            # Create data channel
            data_channel = pc.createDataChannel("libp2p", ordered=True)
            
            # Set up connection event handling
            connection_established = trio.Event()
            connection_error = trio.Event()
            error_message = ""
            
            def on_connection_state_change():
                logger.debug(f"WebRTC connection state: {pc.connectionState}")
                if pc.connectionState == "connected":
                    trio.from_thread.run_sync(connection_established.set)
                elif pc.connectionState in ["failed", "closed"]:
                    trio.from_thread.run_sync(connection_error.set)
            
            pc.addEventListener("connectionstatechange", on_connection_state_change)
            
            # Set up ICE handling
            ice_candidates = []
            
            def on_ice_candidate(event):
                if event.candidate:
                    ice_candidates.append(event.candidate)
                    # Send ICE candidate via signaling
                    trio.from_thread.run_sync(
                        self.signaling_manager.send_ice_candidate,
                        peer_id,
                        event.candidate
                    )
            
            pc.addEventListener("icecandidate", on_ice_candidate)
            
            # Register connection handler for this peer
            async def connection_handler(message_type: str, message):
                nonlocal error_message
                try:
                    if message_type == "answer":
                        answer = dict_to_sdp(message.data)
                        await trio.to_thread.run_sync(self._set_remote_description, pc, answer)
                    elif message_type == "ice-candidate":
                        candidate = dict_to_ice_candidate(message.data)
                        await trio.to_thread.run_sync(self._add_ice_candidate, pc, candidate)
                except Exception as e:
                    error_message = str(e)
                    await connection_error.set()
            
            self.signaling_manager.register_connection_handler(peer_id, connection_handler)
            
            try:
                # Create and send offer
                offer = await trio.to_thread.run_sync(self._create_offer, pc)
                await self.signaling_manager.send_offer(peer_id, offer)
                
                # Wait for connection to be established or fail
                async with trio.move_on_after(30):  # 30 second timeout
                    while True:
                        await trio.sleep(0.1)
                        if connection_established.is_set():
                            break
                        if connection_error.is_set():
                            raise WebRTCConnectionError(f"Connection failed: {error_message}")
                
                if not connection_established.is_set():
                    raise WebRTCConnectionError("Connection timeout")
                
                # Create and return WebRTC connection wrapper
                return WebRTCConnection(data_channel, is_initiator=True)
                
            finally:
                self.signaling_manager.unregister_connection_handler(peer_id)
            
        except Exception as e:
            logger.error(f"Failed to dial WebRTC peer {maddr}: {e}")
            if peer_id in self._connections:
                del self._connections[peer_id]
            raise WebRTCConnectionError(f"WebRTC dial failed: {e}") from e
    
    def _extract_peer_id(self, maddr: Multiaddr) -> Optional[str]:
        """Extract peer ID from multiaddr."""
        try:
            for protocol in maddr.protocols():
                if protocol.name == "p2p":
                    return protocol.value
            return None
        except Exception:
            return None
    
    def _create_offer(self, pc: RTCPeerConnection) -> RTCSessionDescription:
        """Create WebRTC offer (runs in asyncio context)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _do_create_offer():
                offer = await pc.createOffer()
                await pc.setLocalDescription(offer)
                return offer
            
            return loop.run_until_complete(_do_create_offer())
        finally:
            loop.close()
    
    def _set_remote_description(self, pc: RTCPeerConnection, description: RTCSessionDescription) -> None:
        """Set remote description (runs in asyncio context)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(pc.setRemoteDescription(description))
        finally:
            loop.close()
    
    def _add_ice_candidate(self, pc: RTCPeerConnection, candidate) -> None:
        """Add ICE candidate (runs in asyncio context)."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(pc.addIceCandidate(candidate))
        finally:
            loop.close()
    
    def create_listener(self, handler_function: THandler) -> IListener:
        """Create a WebRTC listener."""
        return WebRTCListener(handler_function, self.signaling_manager)
