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
    
    def __init__(self):
        self._ice_servers = [
            RTCIceServer(urls="stun:stun.l.google.com:19302"),
            RTCIceServer(urls="stun:stun1.l.google.com:19302"),
        ]
        self._rtc_config = RTCConfiguration(iceServers=self._ice_servers)
    
    async def dial(self, maddr: Multiaddr) -> IRawConnection:
        """Dial a peer using WebRTC."""
        logger.debug(f"Dialing WebRTC peer at {maddr}")
        
        if not is_valid_webrtc_multiaddr(maddr):
            raise WebRTCConnectionError(f"Invalid WebRTC multiaddr: {maddr}")
        
        try:
            # Create RTCPeerConnection
            pc = RTCPeerConnection(configuration=self._rtc_config)
            
            # Create data channel
            data_channel = pc.createDataChannel("libp2p")
            
            # Set up ICE gathering
            ice_candidates = []
            ice_gathering_complete = trio.Event()
            
            def on_ice_candidate(event):
                if event.candidate:
                    ice_candidates.append(event.candidate)
                else:
                    trio.from_thread.run_sync(ice_gathering_complete.set)
            
            pc.addEventListener("icecandidate", on_ice_candidate)
            
            # Create offer
            offer = await trio.to_thread.run_sync(self._create_offer, pc)
            
            # Wait for ICE gathering to complete
            await ice_gathering_complete.wait()
            
            # Here we would typically:
            # 1. Send offer + ICE candidates via circuit relay signaling
            # 2. Receive answer + remote ICE candidates
            # 3. Set remote description and add ICE candidates
            # 4. Wait for connection to be established
            
            # For now, this is a placeholder - actual signaling integration needed
            raise WebRTCSignalingError("WebRTC signaling not yet implemented")
            
        except Exception as e:
            logger.error(f"Failed to dial WebRTC peer {maddr}: {e}")
            raise WebRTCConnectionError(f"WebRTC dial failed: {e}") from e
    
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
    
    def create_listener(self, handler_function: THandler) -> IListener:
        """Create a WebRTC listener."""
        return WebRTCListener(handler_function)
