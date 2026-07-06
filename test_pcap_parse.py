#!/usr/bin/env python3
"""Test CAT62 parser with generated PCAP data"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from parser_server import Asterix62

# Read first CAT62 record from generated PCAP
with open('sample_radar.pcap', 'rb') as f:
    # Skip PCAP header
    f.read(24)
    
    # Read first packet
    pkt_hdr = f.read(16)
    
    # Read packet data
    import struct
    # Packet header format: ts_sec, ts_usec, incl_len, orig_len (all little-endian in little-endian PCAP)
    incl_len = struct.unpack('<I', pkt_hdr[8:12])[0]
    print(f"Included length from header: {incl_len}")
    
    pkt_data = f.read(incl_len)
    print(f"Actually read: {len(pkt_data)} bytes")
    
    # Extract UDP payload manually
    # Ethernet: skip 14 bytes
    eth_type = struct.unpack('>H', pkt_data[12:14])[0]
    print(f"Ethernet type: {hex(eth_type)}")
    
    if eth_type == 0x0800:
        ip_start = 14
        ihl = (pkt_data[ip_start] & 0x0F) * 4
        proto = pkt_data[ip_start + 9]
        print(f"IP protocol: {proto} (17=UDP)")
        
        udp_start = ip_start + ihl
        udp_len = struct.unpack('>H', pkt_data[udp_start+4:udp_start+6])[0]
        print(f"UDP length: {udp_len}")
        
        payload_start = udp_start + 8
        cat62_payload = pkt_data[payload_start:]
        
        print(f"\nCAT62 Payload ({len(cat62_payload)} bytes):")
        print(f"  Hex: {cat62_payload.hex()}")
        print(f"  Bytes: {list(cat62_payload)}")
        
        # Parse with Asterix62
        parser = Asterix62(cat62_payload)
        print(f"\nParser OK: {parser.ok}")
        print(f"Error: {parser.error_msg}")
        print(f"Items: {parser.items}")
        
        if parser.items:
            for key, value in parser.items.items():
                print(f"  {key}: {value}")
