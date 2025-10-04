import pytest
import trio
from unittest.mock import Mock, patch, AsyncMock
from multiaddr import Multiaddr
from libp2p.transport.webrtc.transport import WebRTCTransport
from libp2p.transport.webrtc.exceptions import WebRTCConnectionError, WebRTCSignalingError
from libp2p.transport.webrtc.signaling import WebRTCSignalingManager

class TestWebRTCTransport:
    def test_transport_creation(self):
        """Test WebRTC transport can be created."""
        transport = WebRTCTransport()
        assert transport is not None
        assert transport._ice_servers is not None
        assert transport._rtc_config is not None
    
    def test_transport_creation_with_signaling(self):
        """Test WebRTC transport can be created with signaling manager."""
        mock_signaling = Mock(spec=WebRTCSignalingManager)
        transport = WebRTCTransport(signaling_manager=mock_signaling)
        assert transport.signaling_manager == mock_signaling
    
    def test_create_listener(self):
        """Test listener creation."""
        transport = WebRTCTransport()
        
        async def dummy_handler(conn):
            pass
        
        listener = transport.create_listener(dummy_handler)
        assert listener is not None
        assert listener.handler == dummy_handler
    
    @pytest.mark.trio
    async def test_dial_invalid_multiaddr(self):
        """Test dialing with invalid multiaddr raises error."""
        transport = WebRTCTransport()
        invalid_maddr = Multiaddr("/tcp/8080")
        
        with pytest.raises(WebRTCConnectionError):
            await transport.dial(invalid_maddr)
    
    @pytest.mark.trio
    async def test_dial_without_signaling_manager(self):
        """Test dialing without signaling manager raises error."""
        transport = WebRTCTransport()
        valid_maddr = Multiaddr("/webrtc/p2p/QmTest123")
        
        with pytest.raises(WebRTCSignalingError):
            await transport.dial(valid_maddr)
    
    @pytest.mark.trio
    async def test_dial_with_signaling_manager(self):
        """Test dialing with signaling manager."""
        mock_signaling = Mock(spec=WebRTCSignalingManager)
        mock_signaling.send_offer = AsyncMock()
        mock_signaling.register_connection_handler = Mock()
        mock_signaling.unregister_connection_handler = Mock()
        
        transport = WebRTCTransport(signaling_manager=mock_signaling)
        
        # Mock multiaddr with peer ID
        test_maddr = Multiaddr("/webrtc/p2p/QmTest123")
        
        # This should fail due to no actual WebRTC peer, but test the setup
        with pytest.raises(WebRTCConnectionError):
            await transport.dial(test_maddr)
        
        # Verify signaling manager methods were called
        mock_signaling.register_connection_handler.assert_called()
        mock_signaling.unregister_connection_handler.assert_called()
    
    def test_extract_peer_id_valid(self):
        """Test peer ID extraction from valid multiaddr."""
        transport = WebRTCTransport()
        maddr = Multiaddr("/webrtc/p2p/QmTest123")
        
        peer_id = transport._extract_peer_id(maddr)
        assert peer_id == "QmTest123"
    
    def test_extract_peer_id_invalid(self):
        """Test peer ID extraction from invalid multiaddr."""
        transport = WebRTCTransport()
        maddr = Multiaddr("/webrtc")
        
        peer_id = transport._extract_peer_id(maddr)
        assert peer_id is None
    
    @patch('libp2p.transport.webrtc.transport.RTCPeerConnection')
    def test_create_offer(self, mock_pc_class):
        """Test offer creation."""
        transport = WebRTCTransport()
        mock_pc = Mock()
        mock_pc_class.return_value = mock_pc
        
        # Mock asyncio methods
        with patch('asyncio.new_event_loop') as mock_loop_factory:
            mock_loop = Mock()
            mock_loop_factory.return_value = mock_loop
            mock_loop.run_until_complete = Mock(return_value="test-offer")
            
            result = transport._create_offer(mock_pc)
            assert result == "test-offer"
            mock_loop.close.assert_called_once()

class TestWebRTCTransportIntegration:
    @pytest.mark.trio
    async def test_transport_registry_integration(self):
        """Test WebRTC transport can be created via registry."""
        from libp2p.transport.transport_registry import create_transport_for_multiaddr
        from libp2p.transport.upgrader import TransportUpgrader
        from libp2p.transport.webrtc.utils import create_webrtc_multiaddr
        
        # Mock upgrader for test
        upgrader = None  # WebRTC doesn't require upgrader for basic functionality
        
        maddr = create_webrtc_multiaddr()
        transport = create_transport_for_multiaddr(maddr, upgrader)
        
        assert transport is not None
        assert isinstance(transport, WebRTCTransport)
    
    @pytest.mark.trio
    async def test_webrtc_direct_transport_registry(self):
        """Test WebRTC-direct transport creation via registry."""
        from libp2p.transport.transport_registry import create_transport_for_multiaddr
        from libp2p.transport.webrtc.utils import create_webrtc_multiaddr
        
        upgrader = None
        maddr = create_webrtc_multiaddr("127.0.0.1", 4242)
        transport = create_transport_for_multiaddr(maddr, upgrader)
        
        assert transport is not None
        assert isinstance(transport, WebRTCTransport)