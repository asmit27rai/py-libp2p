import pytest
from multiaddr import Multiaddr
from libp2p.transport.webrtc.utils import is_valid_webrtc_multiaddr, create_webrtc_multiaddr

class TestWebRTCUtils:
    def test_valid_webrtc_multiaddr(self):
        """Test validation of WebRTC multiaddrs."""
        # Valid WebRTC multiaddrs
        valid_addrs = [
            "/webrtc",
            "/ip4/127.0.0.1/udp/0/webrtc-direct",
            "/ip6/::1/udp/4242/webrtc-direct",
        ]
        
        for addr_str in valid_addrs:
            maddr = Multiaddr(addr_str)
            assert is_valid_webrtc_multiaddr(maddr), f"Should be valid: {addr_str}"
    
    def test_invalid_webrtc_multiaddr(self):
        """Test validation rejects invalid multiaddrs."""
        invalid_addrs = [
            "/tcp/8080",
            "/ip4/127.0.0.1/tcp/8080/webrtc",  # Missing UDP
            "/webrtc-direct",  # Missing IP/UDP
            "/ip4/127.0.0.1/webrtc-direct",  # Missing UDP
        ]
        
        for addr_str in invalid_addrs:
            maddr = Multiaddr(addr_str)
            assert not is_valid_webrtc_multiaddr(maddr), f"Should be invalid: {addr_str}"
    
    def test_create_webrtc_multiaddr(self):
        """Test WebRTC multiaddr creation."""
        # Private-to-private
        maddr1 = create_webrtc_multiaddr()
        assert str(maddr1) == "/webrtc"
        
        # Direct connection  
        maddr2 = create_webrtc_multiaddr("127.0.0.1", 4242)
        assert str(maddr2) == "/ip4/127.0.0.1/udp/4242/webrtc-direct"
