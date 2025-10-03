import pytest
import trio
from multiaddr import Multiaddr
from libp2p.transport.webrtc.transport import WebRTCTransport
from libp2p.transport.webrtc.utils import create_webrtc_multiaddr

class TestWebRTCIntegration:
    @pytest.mark.trio
    async def test_transport_registry_integration(self):
        """Test WebRTC transport can be created via registry."""
        from libp2p.transport.transport_registry import create_transport_for_multiaddr
        from libp2p.transport.upgrader import TransportUpgrader
        
        # Mock upgrader for test
        upgrader = None  # WebRTC doesn't require upgrader for basic functionality
        
        maddr = create_webrtc_multiaddr()
        transport = create_transport_for_multiaddr(maddr, upgrader)
        
        assert transport is not None
        assert isinstance(transport, WebRTCTransport)