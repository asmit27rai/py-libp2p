# WebRTC Transport for py-libp2p

This document describes the WebRTC transport implementation for py-libp2p, which enables peer-to-peer communication over WebRTC DataChannels with NAT traversal capabilities.

## Overview

The WebRTC transport provides:

- **Private-to-private connectivity**: Direct peer-to-peer connections without requiring open ports
- **NAT traversal**: Built-in ICE, STUN, and TURN support for connections behind firewalls
- **Cross-platform compatibility**: Works with browser-based libp2p nodes and other language implementations
- **Signaling flexibility**: Support for both WebSocket and circuit relay signaling

## Architecture

The WebRTC transport consists of several key components:

### Core Components

1. **WebRTCTransport**: Main transport implementation that handles connection establishment
2. **WebRTCListener**: Accepts incoming WebRTC connections via signaling
3. **WebRTCConnection**: Wraps aiortc DataChannel to implement libp2p IRawConnection interface
4. **WebRTCSignalingManager**: Manages SDP offer/answer exchange and ICE candidate sharing

### Signaling Channels

1. **WebSocketSignalingChannel**: WebSocket-based signaling for browser compatibility
2. **CircuitRelaySignalingChannel**: Uses libp2p circuit relay for signaling (decentralized)

## Installation

Install py-libp2p with WebRTC support:

```bash
pip install "libp2p[webrtc]"
```

This installs the following dependencies:

- `aiortc>=1.6.0` - WebRTC implementation
- `aioice>=0.9.0` - ICE support
- `av>=10.0.0` - Media processing
- `websockets>=11.0.0` - WebSocket signaling

## Usage

### Basic Example

```python
import trio
from libp2p import new_host
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.transport.webrtc import (
    WebRTCTransport,
    WebRTCSignalingManager,
    WebSocketSignalingChannel
)
from libp2p.transport.webrtc.utils import create_webrtc_multiaddr

async def main():
    # Create host
    key_pair = create_new_key_pair()
    host = new_host(key_pair=key_pair)

    # Set up WebRTC signaling
    signaling_url = "ws://localhost:8765"
    signaling_channel = WebSocketSignalingChannel(signaling_url, str(host.get_id()))
    signaling_manager = WebRTCSignalingManager(signaling_channel, str(host.get_id()))

    # Create WebRTC transport
    webrtc_transport = WebRTCTransport(signaling_manager=signaling_manager)

    # Register transport
    from libp2p.transport.transport_registry import register_transport
    register_transport("webrtc", type(webrtc_transport))

    # Listen on WebRTC
    webrtc_addr = create_webrtc_multiaddr()

    async with host.run([webrtc_addr]):
        await signaling_manager.setup()
        print(f"Listening on: {webrtc_addr}/p2p/{host.get_id()}")
        await trio.sleep_forever()

trio.run(main)
```

### WebRTC MultiAddrs

The WebRTC transport supports two multiaddr formats:

1. **Private-to-private**: `/webrtc` - Uses signaling for connection establishment
2. **Direct connection**: `/ip4/127.0.0.1/udp/4242/webrtc-direct` - Direct UDP-based connection

### Signaling Setup

#### WebSocket Signaling

For browser compatibility and simple deployment:

```python
from libp2p.transport.webrtc.signaling import (
    WebRTCSignalingManager,
    WebSocketSignalingChannel
)

# Client setup
signaling_url = "ws://signaling-server:8765"
signaling_channel = WebSocketSignalingChannel(signaling_url, peer_id)
signaling_manager = WebRTCSignalingManager(signaling_channel, peer_id)

transport = WebRTCTransport(signaling_manager=signaling_manager)
```

#### Circuit Relay Signaling

For fully decentralized signaling:

```python
from libp2p.transport.webrtc.signaling import (
    WebRTCSignalingManager,
    CircuitRelaySignalingChannel
)

# Setup with existing libp2p host
relay_peer_id = "QmRelayPeerID..."
signaling_channel = CircuitRelaySignalingChannel(host, relay_peer_id)
signaling_manager = WebRTCSignalingManager(signaling_channel, str(host.get_id()))

transport = WebRTCTransport(signaling_manager=signaling_manager)
```

## Advanced Configuration

### Custom ICE Servers

```python
from aiortc import RTCIceServer, RTCConfiguration

# Custom STUN/TURN servers
ice_servers = [
    RTCIceServer(urls="stun:your-stun-server.com:3478"),
    RTCIceServer(
        urls="turn:your-turn-server.com:3478",
        username="user",
        credential="pass"
    )
]

config = RTCConfiguration(iceServers=ice_servers)
transport = WebRTCTransport()
transport._rtc_config = config
```

### Connection Handling

```python
async def stream_handler(stream):
    """Handle incoming streams."""
    try:
        while True:
            data = await stream.read()
            if not data:
                break

            # Process data
            response = process_message(data)
            await stream.write(response)

    except Exception as e:
        logger.error(f"Stream error: {e}")
    finally:
        await stream.close()

# Register handler
host.set_stream_handler("/my-protocol/1.0.0", stream_handler)
```

## Integration with libp2p

The WebRTC transport integrates seamlessly with the libp2p ecosystem:

### Transport Registry

The transport automatically registers with the transport registry:

```python
from libp2p.transport.transport_registry import create_transport_for_multiaddr

# Automatically selects WebRTC transport for webrtc multiaddrs
maddr = Multiaddr("/webrtc/p2p/QmPeerID...")
transport = create_transport_for_multiaddr(maddr, upgrader)
```

### Stream Multiplexing

WebRTC connections support the standard libp2p stream multiplexing:

```python
# Multiple streams over single WebRTC connection
stream1 = await host.new_stream(peer_id, ["/protocol1/1.0.0"])
stream2 = await host.new_stream(peer_id, ["/protocol2/1.0.0"])
```

### Security

WebRTC DataChannels provide built-in encryption (DTLS), which integrates with libp2p's security protocols.

## Examples

### Echo Server

See `examples/webrtc_echo.py` for a complete example that includes:

- Simple WebSocket signaling server
- WebRTC echo server
- WebRTC client
- Full connection establishment flow

### Running the Example

```bash
# Terminal 1: Start signaling server
python examples/webrtc_echo.py --signaling-server

# Terminal 2: Start echo server
python examples/webrtc_echo.py --listen --signaling ws://localhost:8765

# Terminal 3: Connect as client
python examples/webrtc_echo.py --connect /webrtc/p2p/QmPeerID --signaling ws://localhost:8765
```

## Browser Compatibility

The WebRTC transport is designed to be compatible with browser-based libp2p implementations:

### JavaScript Interoperability

```javascript
// Browser-side JavaScript libp2p
import { createLibp2p } from "libp2p";
import { webRTC } from "@libp2p/webrtc";
import { webSockets } from "@libp2p/websockets";

const node = await createLibp2p({
  transports: [webRTC(), webSockets()],
  // ... other config
});

// Can connect to py-libp2p WebRTC nodes
await node.dial("/webrtc/p2p/QmPythonPeerID...");
```

### Signaling Protocol

The signaling protocol is compatible with standard WebRTC signaling used in browsers:

```json
{
  "type": "offer",
  "to": "target-peer-id",
  "from": "sender-peer-id",
  "data": {
    "type": "offer",
    "sdp": "v=0\\r\\no=- ..."
  }
}
```

## Troubleshooting

### Common Issues

1. **Connection Timeout**

   - Check ICE server configuration
   - Verify signaling server connectivity
   - Ensure proper firewall/NAT configuration

2. **Signaling Errors**

   - Verify signaling server is running
   - Check WebSocket URL format
   - Confirm peer IDs are correct

3. **Data Channel Errors**
   - Ensure proper async/trio integration
   - Check for connection state before writing
   - Handle connection closed exceptions

### Debug Logging

Enable detailed logging:

```python
import logging
logging.getLogger("libp2p.transport.webrtc").setLevel(logging.DEBUG)
logging.getLogger("aiortc").setLevel(logging.DEBUG)
```

### Performance Considerations

- WebRTC connections have higher latency than TCP during establishment
- Consider connection pooling for frequent communication
- Use appropriate STUN/TURN servers for your network topology
- Monitor ICE connection state for connection health

## Future Enhancements

Planned improvements include:

1. **Enhanced NAT traversal**: Better ICE candidate gathering and prioritization
2. **Performance optimization**: Reduced connection establishment time
3. **Mobile support**: Optimizations for mobile networks
4. **Advanced signaling**: Support for additional signaling mechanisms
5. **Metrics and monitoring**: Connection quality and performance metrics

## Contributing

To contribute to the WebRTC transport implementation:

1. Ensure all tests pass: `pytest libp2p/transport/webrtc/`
2. Add tests for new functionality
3. Update documentation for API changes
4. Follow the existing code style and patterns

For issues and feature requests, please use the py-libp2p GitHub repository.
