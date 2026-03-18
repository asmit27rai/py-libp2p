"""
Private-to-private WebRTC transport implementation.
Uses circuit relays for signaling and establishes direct WebRTC connections.
"""

try:
    from .transport import WebRTCTransport
except ImportError:
    WebRTCTransport = None

try:
    from ..multiaddr_protocols import register_webrtc_protocols
    register_webrtc_protocols()
except Exception as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Could not register WebRTC multiaddr protocols: {e}")

__all__ = ["WebRTCTransport"]
