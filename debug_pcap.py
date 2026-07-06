#!/usr/bin/env python3
"""Debug script to inspect generated PCAP file"""
import struct
from pathlib import Path

pcap_file = Path('sample_radar.pcap')

if not pcap_file.exists():
    print(f"PCAP file not found: {pcap_file}")
    exit(1)

with open(pcap_file, 'rb') as f:
    # Read PCAP global header
    gh = f.read(24)
    if len(gh) != 24:
        print("Invalid PCAP header")
        exit(1)
    
    magic = struct.unpack('I', gh[0:4])[0]
    print(f"PCAP Magic: {hex(magic)}")
    
    if magic == 0xa1b2c3d4:
        endian = '<'
        print("Little-endian PCAP")
    elif magic == 0xd4c3b2a1:
        endian = '>'
        print("Big-endian PCAP")
    else:
        print(f"Unknown magic: {hex(magic)}")
        exit(1)
    
    version_major, version_minor = struct.unpack(endian + 'HH', gh[4:8])
    print(f"Version: {version_major}.{version_minor}")
    
    linktype = struct.unpack(endian + 'I', gh[20:24])[0]
    print(f"Link type: {linktype} (1=Ethernet)")
    
    # Read first few packets
    print("\nFirst 5 packets:")
    for pkt_idx in range(5):
        hdr = f.read(16)
        if len(hdr) != 16:
            print("End of file")
            break
        
        ts_sec, ts_usec, incl_len, orig_len = struct.unpack(endian + 'IIII', hdr)
        print(f"\nPacket {pkt_idx}:")
        print(f"  Timestamp: {ts_sec}.{ts_usec}")
        print(f"  Captured length: {incl_len}, Original: {orig_len}")
        
        # Read packet data
        pkt_data = f.read(incl_len)
        
        if len(pkt_data) < 14:
            print("  Error: Packet too short for Ethernet header")
            continue
        
        # Parse Ethernet header
        dest_mac = pkt_data[0:6].hex()
        src_mac = pkt_data[6:12].hex()
        eth_type = struct.unpack('>H', pkt_data[12:14])[0]
        print(f"  Ethernet: {src_mac} -> {dest_mac}, type={hex(eth_type)}")
        
        if eth_type != 0x0800:
            print("  Not IPv4")
            continue
        
        # Parse IPv4 header
        if len(pkt_data) < 34:
            print("  Error: Packet too short for IPv4 header")
            continue
        
        ip_start = 14
        ver_ihl = pkt_data[ip_start]
        ihl = (ver_ihl & 0x0F) * 4
        proto = pkt_data[ip_start + 9]
        src_ip = '.'.join(str(b) for b in pkt_data[ip_start+12:ip_start+16])
        dst_ip = '.'.join(str(b) for b in pkt_data[ip_start+16:ip_start+20])
        print(f"  IPv4: {src_ip} -> {dst_ip}, proto={proto}, IHL={ihl}")
        
        if proto != 17:  # UDP
            print("  Not UDP")
            continue
        
        # Parse UDP header
        udp_start = ip_start + ihl
        if len(pkt_data) < udp_start + 8:
            print("  Error: Packet too short for UDP header")
            continue
        
        src_port, dst_port, udp_len = struct.unpack('>HHH', pkt_data[udp_start:udp_start+6])
        print(f"  UDP: {src_port} -> {dst_port}, length={udp_len}")
        
        # Parse CAT62 record
        payload_start = udp_start + 8
        payload = pkt_data[payload_start:]
        
        if len(payload) > 0:
            cat = payload[0]
            print(f"  CAT62: category={cat}")
            if cat == 62 and len(payload) >= 3:
                rec_len = struct.unpack('>H', payload[1:3])[0]
                print(f"    Record length: {rec_len}")
                if len(payload) >= 4:
                    fspec = payload[3]
                    print(f"    FSPEC: {bin(fspec)}")
