from libp2p.exceptions import BaseLibp2pError

class WebRTCError(BaseLibp2pError):
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

class WebRTCTimeoutError(WebRTCError):
    """Raised when WebRTC operations timeout."""
    pass

class WebRTCIceError(WebRTCError):
    """Raised when ICE gathering or connection fails."""
    pass

class WebRTCSignalingChannelError(WebRTCSignalingError):
    """Raised when signaling channel operations fail."""
    pass