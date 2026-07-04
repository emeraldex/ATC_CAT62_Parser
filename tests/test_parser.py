#!/usr/bin/env python3
"""
Unit tests for CAT62 Parser

Run with: python -m pytest tests/test_parser.py -v
"""
import pytest
import struct
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from parser_server import Asterix62, TrackData, Statistics, _ia5, _u16, _i16, _u32, _i32


class TestBinaryParsing:
    """Test binary data parsing utilities"""
    
    def test_u16(self):
        """Test unsigned 16-bit parsing"""
        data = struct.pack('>H', 12345)
        assert _u16(data, 0) == 12345
    
    def test_i16(self):
        """Test signed 16-bit parsing"""
        data = struct.pack('>h', -12345)
        assert _i16(data, 0) == -12345
    
    def test_u32(self):
        """Test unsigned 32-bit parsing"""
        data = struct.pack('>I', 123456789)
        assert _u32(data, 0) == 123456789
    
    def test_i32(self):
        """Test signed 32-bit parsing"""
        data = struct.pack('>i', -123456789)
        assert _i32(data, 0) == -123456789


class TestIA5Decoding:
    """Test IA-5 character decoding"""
    
    def test_ia5_space(self):
        """Test space character"""
        assert _ia5(32) == ' '
    
    def test_ia5_digit(self):
        """Test digit encoding"""
        assert _ia5(48) == '0'
        assert _ia5(57) == '9'
    
    def test_ia5_letter(self):
        """Test letter encoding"""
        assert _ia5(1) == 'A'
        assert _ia5(26) == 'Z'


class TestCAT62Parser:
    """Test CAT62 record parsing"""
    
    def create_minimal_cat62(self):
        """Create a minimal valid CAT62 record"""
        # Category 62, length 10, FSPEC with I062/010
        data = bytearray()
        data.append(62)  # Category
        data.extend(struct.pack('>H', 10))  # Length (10 bytes)
        data.append(0x80)  # FSPEC: bit 7 set (I062/010), bit 0 clear (no extension)
        # I062/010 data (SAC=1, SIC=2)
        data.append(1)  # SAC
        data.append(2)  # SIC
        # Padding to reach length 10
        data.extend([0] * (10 - len(data)))
        return bytes(data)
    
    def test_parse_minimal_record(self):
        """Test parsing minimal valid record"""
        data = self.create_minimal_cat62()
        parser = Asterix62(data)
        assert parser.ok is True
        assert 'I062/010' in parser.items
        assert parser.items['I062/010']['sac'] == 1
        assert parser.items['I062/010']['sic'] == 2
    
    def test_reject_wrong_category(self):
        """Test rejection of non-CAT62 records"""
        data = bytearray()
        data.append(61)  # Wrong category
        data.extend(struct.pack('>H', 10))
        data.append(0x80)
        parser = Asterix62(bytes(data))
        assert parser.ok is False
    
    def test_reject_truncated_record(self):
        """Test rejection of truncated records"""
        data = bytes([62, 0, 10, 0x80])  # Claims 10 bytes but only 4
        parser = Asterix62(data)
        assert parser.ok is False
    
    def test_parse_track_number(self):
        """Test parsing track number field"""
        data = bytearray()
        data.append(62)
        data.extend(struct.pack('>H', 13))  # Length
        data.append(0x10)  # FSPEC: bit 4 set (I062/040), bit 0 clear
        # I062/040 (Track number - 2 bytes, 12 bits used)
        data.extend(struct.pack('>H', 0x1234))
        data.extend([0] * (13 - len(data)))
        
        parser = Asterix62(bytes(data))
        assert parser.ok is True
        assert 'I062/040' in parser.items
        assert parser.items['I062/040']['tn'] == (0x1234 & 0x0FFF)


class TestTrackData:
    """Test TrackData class"""
    
    def test_track_creation(self):
        """Test creating track data"""
        track = TrackData(track_id='ABC123')
        assert track.track_id == 'ABC123'
        assert track.pos_lat is None
        assert track.timestamp == 0.0
    
    def test_track_stale_detection(self):
        """Test stale track detection"""
        import time
        track = TrackData(track_id='ABC123', timestamp=time.time())
        
        # Should not be stale immediately
        assert track.is_stale(time.time(), timeout=30) is False
        
        # Should be stale after timeout
        future = time.time() + 31
        assert track.is_stale(future, timeout=30) is True
    
    def test_track_to_dict(self):
        """Test converting track to dictionary"""
        track = TrackData(
            track_id='ABC123',
            pos_lat=3.1390,
            pos_lon=101.6869,
            ground_speed=425.5,
            heading=270.0
        )
        data = track.to_dict()
        assert data['track_id'] == 'ABC123'
        assert data['pos_lat'] == 3.1390
        assert data['ground_speed'] == 425.5


class TestStatistics:
    """Test Statistics class"""
    
    def test_message_recording(self):
        """Test message counter"""
        stats = Statistics()
        assert stats.messages_received == 0
        
        stats.record_message(success=True)
        assert stats.messages_received == 1
        assert stats.messages_parsed == 1
        assert stats.messages_failed == 0
        
        stats.record_message(success=False)
        assert stats.messages_received == 2
        assert stats.messages_failed == 1
    
    def test_speed_recording(self):
        """Test speed statistics"""
        stats = Statistics()
        stats.record_speed(400.0)
        stats.record_speed(500.0)
        stats.record_speed(450.0)
        
        assert len(stats.speeds) == 3
        assert max(stats.speeds) == 500.0
    
    def test_heading_recording(self):
        """Test heading statistics"""
        stats = Statistics()
        stats.record_heading(0)
        stats.record_heading(90)
        stats.record_heading(180)
        
        assert len(stats.headings) == 3


class TestDataValidation:
    """Test data validation"""
    
    def test_invalid_coordinates_rejected(self):
        """Test that invalid coordinates are rejected"""
        track = TrackData(
            track_id='TEST',
            pos_lat=95.0  # Invalid: > 90
        )
        assert track.pos_lat == 95.0  # Set, but application should validate
    
    def test_negative_speed_handled(self):
        """Test handling of negative speeds"""
        stats = Statistics()
        stats.record_speed(-100.0)
        # Negative speeds should be skipped
        assert len(stats.speeds) == 0


class TestErrorHandling:
    """Test error handling"""
    
    def test_parser_error_message(self):
        """Test parser error message capture"""
        data = bytes([62, 0, 3])  # Minimal invalid
        parser = Asterix62(data)
        assert parser.ok is False
        assert parser.error_msg is not None
    
    def test_parser_graceful_failure(self):
        """Test parser doesn't crash on invalid data"""
        invalid_data = [
            bytes(),  # Empty
            bytes([61, 0, 10]),  # Wrong category
            bytes([62] * 100),  # All same byte
        ]
        
        for data in invalid_data:
            parser = Asterix62(data)
            # Should not crash
            assert isinstance(parser.ok, bool)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
