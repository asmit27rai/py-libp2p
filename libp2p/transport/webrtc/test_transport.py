import pytest
import trio
from multiaddr import Multiaddr
from libp2p.transport.webrtc.transport import WebRTCTransport
from libp2p.transport.webrtc.exceptions import WebRTCConnectionError

class TestWebRTCTransport:
    def test_transport_creation(self):
        """Test WebRTC transport can be created."""
        transport = WebRTCTransport()
        assert transport is not None
    
    def test_create_listener(self):
        """Test listener creation."""
        transport = WebRTCTransport()
        
        async def dummy_handler(conn):
            pass
        
        listener = transport.create_listener(dummy_handler)
        assert listener is not None
    
    @pytest.mark.trio
    async def test_dial_invalid_multiaddr(self):
        """Test dialing with invalid multiaddr raises error."""
        transport = WebRTCTransport()
        invalid_maddr = Multiaddr("/tcp/8080")
        
        with pytest.raises(WebRTCConnectionError):
            await transport.dial(invalid_maddr)