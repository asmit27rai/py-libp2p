"""
WebRTC Echo Example for py-libp2p

This example demonstrates WebRTC private-to-private connectivity.
"""
import asyncio
import logging
import argparse
from multiaddr import Multiaddr
import trio
from libp2p import new_host
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.custom_types import TProtocol
from libp2p.network.stream.net_stream import INetStream
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.transport.webrtc.utils import create_webrtc_multiaddr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROTOCOL_ID = TProtocol("/webrtc-echo/1.0.0")

async def echo_stream_handler(stream: INetStream) -> None:
    """Echo back received messages."""
    try:
        while True:
            data = await stream.read()
            if not data:
                break
            
            logger.info(f"Received: {data.decode('utf-8')}")
            await stream.write(data)
            
    except Exception as e:
        logger.error(f"Error in echo handler: {e}")
    finally:
        await stream.close()

async def run_listener() -> None:
    """Run WebRTC echo server."""
    key_pair = create_new_key_pair()
    host = new_host(key_pair=key_pair)
    
    # Listen on WebRTC
    webrtc_addr = create_webrtc_multiaddr()
    
    async with host.run([webrtc_addr]):
        host.set_stream_handler(PROTOCOL_ID, echo_stream_handler)
        
        print(f"WebRTC Echo Server running!")
        print(f"Peer ID: {host.get_id().to_string()}")
        print(f"Listening on: {webrtc_addr}/p2p/{host.get_id().to_string()}")
        
        await trio.sleep_forever()

async def run_client(target_addr: str) -> None:
    """Run WebRTC echo client."""
    key_pair = create_new_key_pair()
    host = new_host(key_pair=key_pair)
    
    maddr = Multiaddr(target_addr)
    peer_info = info_from_p2p_addr(maddr)
    
    async with host.run([]):
        await host.connect(peer_info)
        
        stream = await host.new_stream(peer_info.peer_id, [PROTOCOL_ID])
        
        message = b"Hello WebRTC World!"
        await stream.write(message)
        
        response = await stream.read()
        await stream.close()
        
        print(f"Sent: {message.decode('utf-8')}")
        print(f"Received: {response.decode('utf-8')}")

def main():
    parser = argparse.ArgumentParser(description="WebRTC Echo Example")
    parser.add_argument("--listen", action="store_true", help="Run as server")
    parser.add_argument("--connect", type=str, help="Connect to peer multiaddr")
    
    args = parser.parse_args()
    
    if args.listen:
        trio.run(run_listener)
    elif args.connect:
        trio.run(run_client, args.connect)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()