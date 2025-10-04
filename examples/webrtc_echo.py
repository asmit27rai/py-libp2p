"""
WebRTC Echo Example for py-libp2p

This example demonstrates WebRTC private-to-private connectivity using the WebRTC transport.
It shows how to set up WebRTC signaling and establish peer-to-peer connections.

Usage:
    # Start a signaling server (simple WebSocket server)
    python examples/webrtc_echo.py --signaling-server

    # Start an echo server
    python examples/webrtc_echo.py --listen --signaling ws://localhost:8765

    # Connect as a client
    python examples/webrtc_echo.py --connect <peer-multiaddr> --signaling ws://localhost:8765

Example:
    # Terminal 1 - Start signaling server
    python examples/webrtc_echo.py --signaling-server

    # Terminal 2 - Start echo server  
    python examples/webrtc_echo.py --listen --signaling ws://localhost:8765

    # Terminal 3 - Connect as client
    python examples/webrtc_echo.py --connect /webrtc/p2p/QmExample123 --signaling ws://localhost:8765
"""
import asyncio
import logging
import argparse
import json
from multiaddr import Multiaddr
import trio
import websockets
from websockets.server import serve as ws_serve
from libp2p import new_host
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.custom_types import TProtocol
from libp2p.network.stream.net_stream import INetStream
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.transport.webrtc.utils import create_webrtc_multiaddr
from libp2p.transport.webrtc.transport import WebRTCTransport
from libp2p.transport.webrtc.signaling import (
    WebRTCSignalingManager,
    WebSocketSignalingChannel
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROTOCOL_ID = TProtocol("/webrtc-echo/1.0.0")

class SimpleSignalingServer:
    """Simple WebSocket-based signaling server for WebRTC coordination."""
    
    def __init__(self, host="localhost", port=8765):
        self.host = host
        self.port = port
        self.clients = {}  # peer_id -> websocket
        
    async def start(self):
        """Start the signaling server."""
        logger.info(f"Starting signaling server on ws://{self.host}:{self.port}")
        
        async def handler(websocket, path):
            await self.handle_client(websocket)
        
        async with ws_serve(handler, self.host, self.port):
            logger.info(f"Signaling server running on ws://{self.host}:{self.port}")
            await trio.sleep_forever()
    
    async def handle_client(self, websocket):
        """Handle a client connection."""
        peer_id = None
        try:
            async for message in websocket:
                data = json.loads(message)
                
                if data.get("type") == "register":
                    peer_id = data["peer_id"]
                    self.clients[peer_id] = websocket
                    logger.info(f"Registered peer: {peer_id}")
                    
                elif data.get("type") in ["offer", "answer", "ice-candidate"]:
                    # Forward message to target peer
                    target_peer = data.get("to")
                    if target_peer in self.clients:
                        await self.clients[target_peer].send(message)
                        logger.debug(f"Forwarded {data['type']} from {data.get('from')} to {target_peer}")
                    else:
                        logger.warning(f"Target peer {target_peer} not found")
                        
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            if peer_id and peer_id in self.clients:
                del self.clients[peer_id]
                logger.info(f"Unregistered peer: {peer_id}")

async def echo_stream_handler(stream: INetStream) -> None:
    """Echo back received messages."""
    try:
        logger.info("New echo connection established")
        while True:
            data = await stream.read()
            if not data:
                break
            
            message = data.decode('utf-8')
            logger.info(f"Received: {message}")
            
            # Echo the message back
            echo_message = f"Echo: {message}"
            await stream.write(echo_message.encode('utf-8'))
            
    except Exception as e:
        logger.error(f"Error in echo handler: {e}")
    finally:
        await stream.close()
        logger.info("Echo connection closed")

async def run_signaling_server() -> None:
    """Run the signaling server."""
    server = SimpleSignalingServer()
    await server.start()

async def run_listener(signaling_url: str) -> None:
    """Run WebRTC echo server."""
    logger.info("Starting WebRTC echo server...")
    
    # Create host with WebRTC transport
    key_pair = create_new_key_pair()
    host = new_host(key_pair=key_pair)
    
    # Set up WebRTC signaling
    signaling_channel = WebSocketSignalingChannel(signaling_url, str(host.get_id()))
    signaling_manager = WebRTCSignalingManager(signaling_channel, str(host.get_id()))
    
    # Create WebRTC transport with signaling
    webrtc_transport = WebRTCTransport(signaling_manager=signaling_manager)
    
    # Register the WebRTC transport
    from libp2p.transport.transport_registry import register_transport
    register_transport("webrtc", type(webrtc_transport))
    
    # Listen on WebRTC
    webrtc_addr = create_webrtc_multiaddr()
    
    async with host.run([webrtc_addr]):
        # Set up signaling
        await signaling_manager.setup()
        
        # Set stream handler
        host.set_stream_handler(PROTOCOL_ID, echo_stream_handler)
        
        peer_addr = f"{webrtc_addr}/p2p/{host.get_id().to_string()}"
        print(f"WebRTC Echo Server running!")
        print(f"Peer ID: {host.get_id().to_string()}")
        print(f"Listening on: {peer_addr}")
        print(f"Signaling via: {signaling_url}")
        
        await trio.sleep_forever()

async def run_client(target_addr: str, signaling_url: str) -> None:
    """Run WebRTC echo client."""
    logger.info(f"Connecting to WebRTC echo server at {target_addr}")
    
    # Create host with WebRTC transport
    key_pair = create_new_key_pair()
    host = new_host(key_pair=key_pair)
    
    # Set up WebRTC signaling
    signaling_channel = WebSocketSignalingChannel(signaling_url, str(host.get_id()))
    signaling_manager = WebRTCSignalingManager(signaling_channel, str(host.get_id()))
    
    # Create WebRTC transport with signaling
    webrtc_transport = WebRTCTransport(signaling_manager=signaling_manager)
    
    # Register the WebRTC transport
    from libp2p.transport.transport_registry import register_transport
    register_transport("webrtc", type(webrtc_transport))
    
    maddr = Multiaddr(target_addr)
    peer_info = info_from_p2p_addr(maddr)
    
    async with host.run([]):
        # Set up signaling
        await signaling_manager.setup()
        
        try:
            # Connect to the peer
            await host.connect(peer_info)
            logger.info(f"Connected to peer: {peer_info.peer_id}")
            
            # Open a stream
            stream = await host.new_stream(peer_info.peer_id, [PROTOCOL_ID])
            logger.info("Stream opened")
            
            # Send some messages
            messages = [
                "Hello WebRTC World!",
                "This is a test message",
                "WebRTC is working!",
                "Goodbye!"
            ]
            
            for message in messages:
                logger.info(f"Sending: {message}")
                await stream.write(message.encode('utf-8'))
                
                # Read the echo response
                response = await stream.read()
                echo_message = response.decode('utf-8')
                logger.info(f"Received: {echo_message}")
                
                # Wait a bit between messages
                await trio.sleep(1)
            
            await stream.close()
            logger.info("Stream closed")
            
        except Exception as e:
            logger.error(f"Error in client: {e}")
            raise

def main():
    parser = argparse.ArgumentParser(description="WebRTC Echo Example")
    parser.add_argument("--signaling-server", action="store_true", 
                       help="Run signaling server")
    parser.add_argument("--listen", action="store_true", 
                       help="Run as echo server")
    parser.add_argument("--connect", type=str, 
                       help="Connect to peer multiaddr")
    parser.add_argument("--signaling", type=str, default="ws://localhost:8765",
                       help="Signaling server URL")
    
    args = parser.parse_args()
    
    if args.signaling_server:
        trio.run(run_signaling_server)
    elif args.listen:
        trio.run(run_listener, args.signaling)
    elif args.connect:
        trio.run(run_client, args.connect, args.signaling)
    else:
        parser.print_help()
        print("\nExample usage:")
        print("  1. Start signaling server: python webrtc_echo.py --signaling-server")
        print("  2. Start echo server: python webrtc_echo.py --listen --signaling ws://localhost:8765")
        print("  3. Connect as client: python webrtc_echo.py --connect /webrtc/p2p/QmExample123 --signaling ws://localhost:8765")

if __name__ == "__main__":
    main()