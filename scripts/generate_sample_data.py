#!/usr/bin/env python3
"""
Generate sample PCAP radar data for testing

This creates a valid PCAP file containing simulated CAT62 records
suitable for testing the parser with the --pcap option.
"""
import struct
import time
import math
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
    """Create a CAT62 record with track data
    
    FSPEC format:
    Octet 1: bit 7=I062/010, bit 6=I062/015, bit 5=I062/020, bit 4=I062/040, 
             bit 3=I062/060, bit 2=I062/070, bit 1=I062/080, bit 0=FX
    Octet 2: bit 7=I062/090, bit 6=I062/100, bit 5=I062/105, bit 4=I062/110,
             bit 3=I062/120, bit 2=I062/135, bit 1=I062/136, bit 0=FX
    Octet 3: bit 7=I062/185, bit 6=I062/200, bit 5=I062/210, bit 4=I062/220,
             bit 3=I062/245, bit 2=I062/270, bit 1=I062/300, bit 0=FX
    """
    record = bytearray()
    record.append(62)  # Category
    
    # Will fill in length later
    length_offset = len(record)
    record.extend([0, 0])
    
    # FSPEC: We need I062/010 (bit 7), I062/040 (bit 4), I062/105 (octet2 bit 5), I062/185 (octet3 bit 7)
    # Octet 1: 10010001 = 0x91 (I062/010 + I062/040 + FX for extension)
    # Octet 2: 00100001 = 0x21 (I062/105 + FX for extension)
    # Octet 3: 10000000 = 0x80 (I062/185, no FX)
    record.append(0x91)  # Octet 1: I062/010, I062/040, FX
    record.append(0x21)  # Octet 2: I062/105, FX
    record.append(0x80)  # Octet 3: I062/185, no extension
    
    # I062/010 (SAC/SIC) - 2 bytes
    record.append(0)  # SAC
    record.append(10)  # SIC
    
    # I062/040 (Track number) - 2 bytes (12 bits in upper bits)
    tn = (track_id & 0x0FFF)
    record.extend(struct.pack('>H', tn << 4))
    
    # I062/105 (Position - WGS84) - 8 bytes (2x 4-byte signed)
    lat_lsb = int(latitude / (180.0 / (1 << 23)))
    lon_lsb = int(longitude / (180.0 / (1 << 23)))
    record.extend(struct.pack('>i', lat_lsb))  # Latitude as signed int32
    record.extend(struct.pack('>i', lon_lsb))  # Longitude as signed int32
    
    # I062/185 (Calculated Track Velocity Cartesian) - 4 bytes
    # Vx and Vy are signed 16-bit integers, each representing m/s with 0.25 m/s per LSB
    # Convert speed (knots) and heading (degrees) to Vx, Vy
    speed_ms = speed * 0.51444  # Convert knots to m/s
    heading_rad = math.radians(heading)
    # Vy = speed * cos(heading), Vx = speed * sin(heading)
    vy_ms = speed_ms * math.cos(heading_rad)
    vx_ms = speed_ms * math.sin(heading_rad)
    # Scale to LSB: 0.25 m/s per LSB, so divide by 0.25 (or multiply by 4)
    vx_lsb = int(round(vx_ms / 0.25))
    vy_lsb = int(round(vy_ms / 0.25))
    # Clamp to 16-bit signed range
    vx_lsb = max(-32768, min(32767, vx_lsb))
    vy_lsb = max(-32768, min(32767, vy_lsb))
    record.extend(struct.pack('>h', vx_lsb))
    record.extend(struct.pack('>h', vy_lsb))
    
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
    samples_dir = Path(__file__).parent.parent / 'samples'
    samples_dir.mkdir(exist_ok=True)
    output = samples_dir / 'sample_radar.pcap'
    print(f"Generating sample PCAP file: {output}")
    generate_sample_pcap(str(output), num_packets=500)
    print(f"Generated {output.stat().st_size} bytes")
    print(f"Usage: python parser_server.py --pcap samples/sample_radar.pcap")
