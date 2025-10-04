"""WebRTC signaling implementation for py-libp2p."""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass
import trio
from aiortc import RTCIceCandidate, RTCSessionDescription

logger = logging.getLogger(__name__)

@dataclass
class SignalingMessage:
    """Base class for signaling messages."""
    message_type: str
    peer_id: str
    data: Dict[str, Any]

@dataclass
class OfferMessage(SignalingMessage):
    """SDP offer message."""
    def __init__(self, peer_id: str, offer: RTCSessionDescription):
        super().__init__("offer", peer_id, {
            "type": offer.type,
            "sdp": offer.sdp
        })

@dataclass
class AnswerMessage(SignalingMessage):
    """SDP answer message."""
    def __init__(self, peer_id: str, answer: RTCSessionDescription):
        super().__init__("answer", peer_id, {
            "type": answer.type,
            "sdp": answer.sdp
        })

@dataclass
class IceCandidateMessage(SignalingMessage):
    """ICE candidate message."""
    def __init__(self, peer_id: str, candidate: RTCIceCandidate):
        super().__init__("ice-candidate", peer_id, {
            "candidate": candidate.candidate,
            "sdpMid": candidate.sdpMid,
            "sdpMLineIndex": candidate.sdpMLineIndex,
        })

class SignalingChannel:
    """Abstract base class for signaling channels."""
    
    async def send_message(self, message: SignalingMessage, target_peer: str) -> None:
        """Send a signaling message to a target peer."""
        raise NotImplementedError()
    
    async def receive_message(self) -> SignalingMessage:
        """Receive a signaling message."""
        raise NotImplementedError()
    
    async def close(self) -> None:
        """Close the signaling channel."""
        raise NotImplementedError()

class WebSocketSignalingChannel(SignalingChannel):
    """WebSocket-based signaling channel."""
    
    def __init__(self, ws_url: str, peer_id: str):
        self.ws_url = ws_url
        self.peer_id = peer_id
        self.websocket = None
        self.message_queue = trio.Queue()
        self._closed = False
    
    async def connect(self) -> None:
        """Connect to the WebSocket signaling server."""
        try:
            import websockets
            self.websocket = await websockets.connect(self.ws_url)
            
            # Register with the signaling server
            await self.websocket.send(json.dumps({
                "type": "register",
                "peer_id": self.peer_id
            }))
            
            # Start receiving messages
            trio.lowlevel.current_task().parent_nursery.start_soon(self._receive_loop)
            
        except Exception as e:
            logger.error(f"Failed to connect to signaling server: {e}")
            raise
    
    async def _receive_loop(self) -> None:
        """Receive messages from WebSocket and queue them."""
        try:
            while not self._closed and self.websocket:
                message_str = await self.websocket.recv()
                message_data = json.loads(message_str)
                
                # Convert to SignalingMessage
                message = self._parse_message(message_data)
                if message:
                    await self.message_queue.put(message)
                    
        except Exception as e:
            if not self._closed:
                logger.error(f"Error in signaling receive loop: {e}")
    
    def _parse_message(self, data: Dict[str, Any]) -> Optional[SignalingMessage]:
        """Parse raw message data into SignalingMessage."""
        message_type = data.get("type")
        peer_id = data.get("from")
        
        if message_type == "offer":
            return OfferMessage(peer_id, RTCSessionDescription(
                sdp=data["data"]["sdp"],
                type=data["data"]["type"]
            ))
        elif message_type == "answer":
            return AnswerMessage(peer_id, RTCSessionDescription(
                sdp=data["data"]["sdp"],
                type=data["data"]["type"]
            ))
        elif message_type == "ice-candidate":
            return IceCandidateMessage(peer_id, RTCIceCandidate(
                candidate=data["data"]["candidate"],
                sdpMid=data["data"]["sdpMid"],
                sdpMLineIndex=data["data"]["sdpMLineIndex"]
            ))
        
        return None
    
    async def send_message(self, message: SignalingMessage, target_peer: str) -> None:
        """Send a signaling message to a target peer."""
        if not self.websocket:
            raise Exception("Not connected to signaling server")
        
        data = {
            "type": message.message_type,
            "to": target_peer,
            "from": self.peer_id,
            "data": message.data
        }
        
        await self.websocket.send(json.dumps(data))
    
    async def receive_message(self) -> SignalingMessage:
        """Receive a signaling message."""
        return await self.message_queue.get()
    
    async def close(self) -> None:
        """Close the signaling channel."""
        self._closed = True
        if self.websocket:
            await self.websocket.close()

class CircuitRelaySignalingChannel(SignalingChannel):
    """Circuit relay-based signaling channel for libp2p."""
    
    def __init__(self, host, relay_peer_id: str):
        self.host = host
        self.relay_peer_id = relay_peer_id
        self.message_queue = trio.Queue()
        self._closed = False
        self.protocol_id = "/webrtc-signaling/1.0.0"
    
    async def setup(self) -> None:
        """Set up the circuit relay signaling."""
        # Set up stream handler for incoming signaling messages
        self.host.set_stream_handler(self.protocol_id, self._handle_signaling_stream)
    
    async def _handle_signaling_stream(self, stream) -> None:
        """Handle incoming signaling messages."""
        try:
            while True:
                data = await stream.read()
                if not data:
                    break
                
                message_data = json.loads(data.decode('utf-8'))
                message = self._parse_message(message_data)
                if message:
                    await self.message_queue.put(message)
                    
        except Exception as e:
            logger.error(f"Error handling signaling stream: {e}")
        finally:
            await stream.close()
    
    def _parse_message(self, data: Dict[str, Any]) -> Optional[SignalingMessage]:
        """Parse raw message data into SignalingMessage."""
        # Similar to WebSocket implementation
        message_type = data.get("type")
        peer_id = data.get("from")
        
        if message_type == "offer":
            return OfferMessage(peer_id, RTCSessionDescription(
                sdp=data["data"]["sdp"],
                type=data["data"]["type"]
            ))
        elif message_type == "answer":
            return AnswerMessage(peer_id, RTCSessionDescription(
                sdp=data["data"]["sdp"],
                type=data["data"]["type"]
            ))
        elif message_type == "ice-candidate":
            return IceCandidateMessage(peer_id, RTCIceCandidate(
                candidate=data["data"]["candidate"],
                sdpMid=data["data"]["sdpMid"],
                sdpMLineIndex=data["data"]["sdpMLineIndex"]
            ))
        
        return None
    
    async def send_message(self, message: SignalingMessage, target_peer: str) -> None:
        """Send a signaling message to a target peer via circuit relay."""
        try:
            # Open stream to target peer through relay
            from libp2p.peer.id import ID
            target_id = ID.from_string(target_peer)
            stream = await self.host.new_stream(target_id, [self.protocol_id])
            
            data = {
                "type": message.message_type,
                "to": target_peer,
                "from": str(self.host.get_id()),
                "data": message.data
            }
            
            await stream.write(json.dumps(data).encode('utf-8'))
            await stream.close()
            
        except Exception as e:
            logger.error(f"Failed to send signaling message: {e}")
            raise
    
    async def receive_message(self) -> SignalingMessage:
        """Receive a signaling message."""
        return await self.message_queue.get()
    
    async def close(self) -> None:
        """Close the signaling channel."""
        self._closed = True

class WebRTCSignalingManager:
    """Manages WebRTC signaling for establishing peer connections."""
    
    def __init__(self, signaling_channel: SignalingChannel, peer_id: str):
        self.signaling_channel = signaling_channel
        self.peer_id = peer_id
        self.pending_offers: Dict[str, RTCSessionDescription] = {}
        self.pending_candidates: Dict[str, List[RTCIceCandidate]] = {}
        self.connection_handlers: Dict[str, Callable] = {}
    
    async def setup(self) -> None:
        """Set up the signaling manager."""
        if hasattr(self.signaling_channel, 'setup'):
            await self.signaling_channel.setup()
        if hasattr(self.signaling_channel, 'connect'):
            await self.signaling_channel.connect()
        
        # Start message processing loop
        trio.lowlevel.current_task().parent_nursery.start_soon(self._process_messages)
    
    async def _process_messages(self) -> None:
        """Process incoming signaling messages."""
        try:
            while True:
                message = await self.signaling_channel.receive_message()
                await self._handle_message(message)
        except Exception as e:
            logger.error(f"Error processing signaling messages: {e}")
    
    async def _handle_message(self, message: SignalingMessage) -> None:
        """Handle a signaling message."""
        if isinstance(message, OfferMessage):
            await self._handle_offer(message)
        elif isinstance(message, AnswerMessage):
            await self._handle_answer(message)
        elif isinstance(message, IceCandidateMessage):
            await self._handle_ice_candidate(message)
    
    async def _handle_offer(self, message: OfferMessage) -> None:
        """Handle incoming offer."""
        self.pending_offers[message.peer_id] = RTCSessionDescription(
            sdp=message.data["sdp"],
            type=message.data["type"]
        )
        
        # Notify connection handler if available
        if message.peer_id in self.connection_handlers:
            handler = self.connection_handlers[message.peer_id]
            await handler("offer", message)
    
    async def _handle_answer(self, message: AnswerMessage) -> None:
        """Handle incoming answer."""
        # Notify connection handler if available
        if message.peer_id in self.connection_handlers:
            handler = self.connection_handlers[message.peer_id]
            await handler("answer", message)
    
    async def _handle_ice_candidate(self, message: IceCandidateMessage) -> None:
        """Handle incoming ICE candidate."""
        if message.peer_id not in self.pending_candidates:
            self.pending_candidates[message.peer_id] = []
        
        candidate = RTCIceCandidate(
            candidate=message.data["candidate"],
            sdpMid=message.data["sdpMid"],
            sdpMLineIndex=message.data["sdpMLineIndex"]
        )
        self.pending_candidates[message.peer_id].append(candidate)
        
        # Notify connection handler if available
        if message.peer_id in self.connection_handlers:
            handler = self.connection_handlers[message.peer_id]
            await handler("ice-candidate", message)
    
    async def send_offer(self, target_peer: str, offer: RTCSessionDescription) -> None:
        """Send an offer to a target peer."""
        message = OfferMessage(self.peer_id, offer)
        await self.signaling_channel.send_message(message, target_peer)
    
    async def send_answer(self, target_peer: str, answer: RTCSessionDescription) -> None:
        """Send an answer to a target peer."""
        message = AnswerMessage(self.peer_id, answer)
        await self.signaling_channel.send_message(message, target_peer)
    
    async def send_ice_candidate(self, target_peer: str, candidate: RTCIceCandidate) -> None:
        """Send an ICE candidate to a target peer."""
        message = IceCandidateMessage(self.peer_id, candidate)
        await self.signaling_channel.send_message(message, target_peer)
    
    def register_connection_handler(self, peer_id: str, handler: Callable) -> None:
        """Register a handler for connection events from a specific peer."""
        self.connection_handlers[peer_id] = handler
    
    def unregister_connection_handler(self, peer_id: str) -> None:
        """Unregister a connection handler."""
        self.connection_handlers.pop(peer_id, None)
    
    async def close(self) -> None:
        """Close the signaling manager."""
        await self.signaling_channel.close()