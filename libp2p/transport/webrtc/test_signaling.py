import pytest
import trio
from unittest.mock import Mock, patch, AsyncMock
from multiaddr import Multiaddr
from libp2p.transport.webrtc.signaling import (
    WebRTCSignalingManager,
    WebSocketSignalingChannel,
    CircuitRelaySignalingChannel,
    OfferMessage,
    AnswerMessage,
    IceCandidateMessage
)

class TestWebRTCSignaling:
    
    @pytest.mark.trio
    async def test_signaling_manager_creation(self):
        """Test signaling manager can be created."""
        mock_channel = Mock()
        manager = WebRTCSignalingManager(mock_channel, "test-peer")
        assert manager.peer_id == "test-peer"
        assert manager.signaling_channel == mock_channel
    
    @pytest.mark.trio
    async def test_offer_message_creation(self):
        """Test offer message creation."""
        with patch('aiortc.RTCSessionDescription') as mock_sdp:
            mock_sdp.type = "offer"
            mock_sdp.sdp = "test-sdp"
            
            message = OfferMessage("peer-123", mock_sdp)
            assert message.message_type == "offer"
            assert message.peer_id == "peer-123"
            assert message.data["type"] == "offer"
            assert message.data["sdp"] == "test-sdp"
    
    @pytest.mark.trio
    async def test_answer_message_creation(self):
        """Test answer message creation."""
        with patch('aiortc.RTCSessionDescription') as mock_sdp:
            mock_sdp.type = "answer"
            mock_sdp.sdp = "test-answer-sdp"
            
            message = AnswerMessage("peer-456", mock_sdp)
            assert message.message_type == "answer"
            assert message.peer_id == "peer-456"
            assert message.data["type"] == "answer"
            assert message.data["sdp"] == "test-answer-sdp"
    
    @pytest.mark.trio
    async def test_ice_candidate_message_creation(self):
        """Test ICE candidate message creation."""
        with patch('aiortc.RTCIceCandidate') as mock_candidate:
            mock_candidate.candidate = "candidate:test"
            mock_candidate.sdpMid = "0"
            mock_candidate.sdpMLineIndex = 0
            
            message = IceCandidateMessage("peer-789", mock_candidate)
            assert message.message_type == "ice-candidate"
            assert message.peer_id == "peer-789"
            assert message.data["candidate"] == "candidate:test"
            assert message.data["sdpMid"] == "0"
            assert message.data["sdpMLineIndex"] == 0

class TestWebSocketSignalingChannel:
    
    def test_websocket_channel_creation(self):
        """Test WebSocket signaling channel creation."""
        channel = WebSocketSignalingChannel("ws://localhost:8080", "peer-123")
        assert channel.ws_url == "ws://localhost:8080"
        assert channel.peer_id == "peer-123"
        assert channel.websocket is None
        assert not channel._closed
    
    @pytest.mark.trio
    async def test_websocket_channel_connect_failure(self):
        """Test WebSocket channel connection failure."""
        channel = WebSocketSignalingChannel("ws://invalid:8080", "peer-123")
        
        with pytest.raises(Exception):
            await channel.connect()

class TestCircuitRelaySignalingChannel:
    
    def test_circuit_relay_channel_creation(self):
        """Test circuit relay signaling channel creation."""
        mock_host = Mock()
        channel = CircuitRelaySignalingChannel(mock_host, "relay-peer")
        assert channel.host == mock_host
        assert channel.relay_peer_id == "relay-peer"
        assert not channel._closed
        assert channel.protocol_id == "/webrtc-signaling/1.0.0"
    
    @pytest.mark.trio
    async def test_circuit_relay_setup(self):
        """Test circuit relay signaling setup."""
        mock_host = Mock()
        mock_host.set_stream_handler = Mock()
        
        channel = CircuitRelaySignalingChannel(mock_host, "relay-peer")
        await channel.setup()
        
        mock_host.set_stream_handler.assert_called_once_with(
            "/webrtc-signaling/1.0.0", 
            channel._handle_signaling_stream
        )