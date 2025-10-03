from multiaddr import Multiaddr
from multiaddr.protocols import Protocol
import json
import logging
from typing import Dict, Any, List
from aiortc import RTCIceCandidate, RTCSessionDescription

logger = logging.getLogger(__name__)

def is_valid_webrtc_multiaddr(maddr: Multiaddr) -> bool:
    """
    Validate that a multiaddr has a valid WebRTC structure.
    
    Valid formats:
    - /ip4/127.0.0.1/udp/0/webrtc-direct
    - /ip6/::1/udp/0/webrtc-direct  
    - /webrtc (for private-to-private)
    """
    try:
        protocols: List[Protocol] = list(maddr.protocols())
        
        # Check for direct WebRTC (private-to-private)
        if len(protocols) == 1 and protocols.name == "webrtc":
            return True
        
        # Check for WebRTC-direct (requires IP + UDP)
        if len(protocols) >= 3:
            if (protocols.name in ["ip4", "ip6"] and 
                protocols.name == "udp" and 
                protocols.name == "webrtc-direct"):
                return True
        
        return False
    except Exception as e:
        logger.error(f"Error validating WebRTC multiaddr {maddr}: {e}")
        return False

def create_webrtc_multiaddr(host: str = None, port: int = None) -> Multiaddr:
    """Create a WebRTC multiaddr."""
    if host and port:
        return Multiaddr(f"/ip4/{host}/udp/{port}/webrtc-direct")
    else:
        return Multiaddr("/webrtc")

def ice_candidate_to_dict(candidate: RTCIceCandidate) -> Dict[str, Any]:
    """Convert RTCIceCandidate to dictionary for transmission."""
    return {
        "candidate": candidate.candidate,
        "sdpMid": candidate.sdpMid,
        "sdpMLineIndex": candidate.sdpMLineIndex,
    }

def dict_to_ice_candidate(data: Dict[str, Any]) -> RTCIceCandidate:
    """Convert dictionary back to RTCIceCandidate."""
    return RTCIceCandidate(
        candidate=data["candidate"],
        sdpMid=data["sdpMid"],
        sdpMLineIndex=data["sdpMLineIndex"]
    )

def sdp_to_dict(description: RTCSessionDescription) -> Dict[str, Any]:
    """Convert RTCSessionDescription to dictionary."""
    return {
        "type": description.type,
        "sdp": description.sdp,
    }

def dict_to_sdp(data: Dict[str, Any]) -> RTCSessionDescription:
    """Convert dictionary to RTCSessionDescription."""
    return RTCSessionDescription(
        sdp=data["sdp"],
        type=data["type"]
    )
