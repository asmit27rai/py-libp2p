from libp2p.transport.exceptions import TransportError

class WebRTCError(TransportError):
    """Base exception for WebRTC transport errors."""
    pass

class WebRTCConnectionError(WebRTCError):
    """Raised when WebRTC connection establishment fails."""
    pass

class WebRTCSignalingError(WebRTCError):
    """Raised when signaling process fails."""
    pass

class WebRTCDataChannelError(WebRTCError):
    """Raised when DataChannel operations fail."""
    pass