#!/usr/bin/env python3
"""
Generate sample PCAP radar data for testing

This creates a valid PCAP file containing simulated CAT62 records
suitable for testing the parser with the --pcap option.
"""
import struct
import time
import io
from pathlib import Path


def create_pcap_header():
    """Create PCAP global header"""
    # PCAP magic number (microsecond resolution)
    magic = 0xa1b2c3d4
    version_major = 2
    version_minor = 4
    timezone = 0
    accuracy = 0
    max_packet_length = 65535
    network = 1  # Ethernet
    
    header = struct.pack(
        '<IHHIIII',
        magic, version_major, version_minor, timezone,
        accuracy, max_packet_length, network
    )
    return header


def create_packet_header(timestamp, packet_data):
    """Create PCAP packet header"""
    ts_sec = int(timestamp)
    ts_usec = int((timestamp - ts_sec) * 1000000)
    captured_len = len(packet_data)
    original_len = len(packet_data)
    
    header = struct.pack(
        '<IIII',
        ts_sec, ts_usec, captured_len, original_len
    )
    return header


def create_eth_frame(payload):
    """Create Ethernet frame with UDP payload"""
    # Ethernet header (14 bytes)
    dest_mac = b'\xff\xff\xff\xff\xff\xff'  # Broadcast
    src_mac = b'\x00\x11\x22\x33\x44\x55'
    eth_type = struct.pack('>H', 0x0800)  # IPv4
    
    eth_frame = dest_mac + src_mac + eth_type
    
    # IPv4 header (20 bytes minimum)
    version_ihl = 0x45  # IPv4, 20-byte header
    dscp_ecn = 0x00
    total_length = 20 + 8 + len(payload)
    identification = 0x1234
    flags_fragment = 0x4000
    ttl = 64
    protocol = 17  # UDP
    src_ip = struct.pack('>BBBB', 192, 168, 1, 100)
    dst_ip = struct.pack('>BBBB', 255, 255, 255, 255)
    
    # Calculate IPv4 checksum
    ip_header = bytearray(20)
    ip_header[0] = version_ihl
    ip_header[1] = dscp_ecn
    ip_header[2:4] = struct.pack('>H', total_length)
    ip_header[4:6] = struct.pack('>H', identification)
    ip_header[6:8] = struct.pack('>H', flags_fragment)
    ip_header[8] = ttl
    ip_header[9] = protocol
    ip_header[12:16] = src_ip
    ip_header[16:20] = dst_ip
    
    # Checksum (simplified)
    checksum = 0
    for i in range(0, 20, 2):
        checksum += struct.unpack('>H', bytes(ip_header[i:i+2]))[0]
    checksum = (~((checksum & 0xffff) + (checksum >> 16))) & 0xffff
    ip_header[10:12] = struct.pack('>H', checksum)
    
    # UDP header (8 bytes)
    src_port = struct.pack('>H', 31002)
    dst_port = struct.pack('>H', 31002)
    udp_length = struct.pack('>H', 8 + len(payload))
    udp_checksum = struct.pack('>H', 0)  # Disabled
    
    udp_header = src_port + dst_port + udp_length + udp_checksum
    
    return eth_frame + bytes(ip_header) + udp_header + payload


def create_cat62_record(track_id, latitude, longitude, speed, heading):
    """Create a CAT62 record with track data"""
    record = bytearray()
    record.append(62)  # Category
    
    # Will fill in length later
    length_offset = len(record)
    record.extend([0, 0])
    
    # FSPEC: I062/010, I062/040, I062/105, I062/060, I062/400
    record.append(0xF8)  # 11111000 - 5 fields + extension
    
    # I062/010 (SAC/SIC)
    record.append(0)  # SAC
    record.append(10)  # SIC
    
    # I062/040 (Track number)
    tn = (track_id & 0x0FFF)
    record.extend(struct.pack('>H', tn << 4))  # 12 bits, padded
    
    # I062/105 (Position - WGS84)
    lat_lsb = latitude / (180.0 / (1 << 23))  # Convert to LSB
    lon_lsb = longitude / (180.0 / (1 << 23))
    record.extend(struct.pack('>i', int(lat_lsb)))  # Signed 32-bit
    record.extend(struct.pack('>i', int(lon_lsb)))
    
    # I062/060 (Track mode 3/A code)
    code_octal = 1234
    record.extend(struct.pack('>H', code_octal))
    
    # I062/400 (Speed/Heading)
    speed_lsb = int(speed / 0.1953125)  # ~0.5 knots per LSB
    heading_lsb = int((heading % 360) / (360.0 / 512))  # 512 values in 360°
    record.extend(struct.pack('>H', speed_lsb & 0xFFF))  # 12 bits
    record.extend(struct.pack('>H', heading_lsb & 0x1FF))  # 9 bits
    
    # Update length
    record_length = len(record)
    record[length_offset:length_offset+2] = struct.pack('>H', record_length)
    
    return bytes(record)


def generate_sample_pcap(output_file, num_packets=100):
    """Generate sample PCAP file with radar data"""
    with open(output_file, 'wb') as f:
        # Write PCAP header
        f.write(create_pcap_header())
        
        # Generate packets
        timestamp = time.time()
        for i in range(num_packets):
            # Create CAT62 record
            track_id = (i % 10) + 1
            latitude = 51.5 + (i % 20) * 0.01
            longitude = -0.1 + (i % 30) * 0.01
            speed = 350.0 + (i % 200)
            heading = (i * 3.6) % 360
            
            cat62_record = create_cat62_record(track_id, latitude, longitude, speed, heading)
            
            # Wrap in Ethernet/IP/UDP
            eth_packet = create_eth_frame(cat62_record)
            
            # Write packet header and data
            packet_timestamp = timestamp + (i * 0.1)  # 100ms between packets
            packet_header = create_packet_header(packet_timestamp, eth_packet)
            f.write(packet_header)
            f.write(eth_packet)


if __name__ == '__main__':
    output = Path(__file__).parent / 'sample_radar.pcap'
    print(f"Generating sample PCAP file: {output}")
    generate_sample_pcap(str(output), num_packets=500)
    print(f"Generated {output.stat().st_size} bytes")
    print(f"Usage: python parser_server.py --pcap sample_radar.pcap")
