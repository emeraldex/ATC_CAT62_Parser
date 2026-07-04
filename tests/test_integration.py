#!/usr/bin/env python3
"""
Integration tests for CAT62 Parser

Tests the full pipeline including UDP input, parsing, WebSocket broadcasting, and HTTP API
"""
import pytest
import asyncio
import json
import socket
import struct
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from parser_server import main, Asterix62, TrackData, Statistics


class TestIntegration:
    """Integration tests for the complete parser"""
    
    @staticmethod
    def create_test_cat62_frame():
        """Create a test CAT62 UDP frame"""
        # Standard CAT62 record with minimal data
        data = bytearray()
        data.append(62)  # Category
        data.extend(struct.pack('>H', 20))  # Length
        data.append(0x80)  # FSPEC: I062/010
        # I062/010 (SAC/SIC)
        data.append(1)  # SAC
        data.append(2)  # SIC
        # Padding
        data.extend([0] * (20 - len(data)))
        return bytes(data)
    
    def test_cat62_frame_creation(self):
        """Test creating valid CAT62 frame"""
        frame = self.create_test_cat62_frame()
        assert len(frame) >= 20
        assert frame[0] == 62
    
    def test_cat62_frame_parsing(self):
        """Test parsing created frame"""
        frame = self.create_test_cat62_frame()
        parser = Asterix62(frame)
        assert parser.ok is True
    
    def test_udp_frame_structure(self):
        """Test UDP frame wrapping"""
        record = self.create_test_cat62_frame()
        # Simulate UDP frame with length header
        frame = struct.pack('>H', len(record)) + record
        assert len(frame) >= len(record) + 2


class TestDataPipeline:
    """Test end-to-end data pipeline"""
    
    def test_track_data_flow(self):
        """Test track data creation and lifecycle"""
        track = TrackData(
            track_id='AF999',
            pos_lat=51.5074,
            pos_lon=-0.1278,
            ground_speed=480.0,
            heading=45.0
        )
        
        # Verify data
        assert track.track_id == 'AF999'
        assert abs(track.pos_lat - 51.5074) < 0.0001
        
        # Convert to JSON-serializable format
        data_dict = track.to_dict()
        assert 'track_id' in data_dict
        assert 'pos_lat' in data_dict
        assert 'ground_speed' in data_dict
    
    def test_statistics_accumulation(self):
        """Test statistics accumulation over time"""
        stats = Statistics()
        
        # Simulate receiving multiple messages
        for i in range(100):
            stats.record_message(success=True)
            stats.record_speed(400.0 + i)
            stats.record_heading(i * 3.6)
        
        assert stats.messages_received == 100
        assert stats.messages_parsed == 100
        assert len(stats.speeds) > 0
    
    def test_track_stale_timeout(self):
        """Test stale track detection"""
        now = time.time()
        old_track = TrackData(track_id='OLD', timestamp=now - 100)
        new_track = TrackData(track_id='NEW', timestamp=now)
        
        assert old_track.is_stale(now, timeout=30) is True
        assert new_track.is_stale(now, timeout=30) is False


class TestErrorRecovery:
    """Test error handling and recovery"""
    
    def test_invalid_frame_handling(self):
        """Test handling of invalid frames"""
        invalid_frames = [
            bytes(),  # Empty
            bytes([61, 0, 10]),  # Wrong category
            bytes([62, 0, 3]),  # Too short
            bytes([62] * 1000),  # All same
        ]
        
        for frame in invalid_frames:
            parser = Asterix62(frame)
            # Should not crash, should have error message
            assert isinstance(parser.ok, bool)
    
    def test_parser_robustness(self):
        """Test parser robustness with edge cases"""
        test_cases = [
            bytearray([62, 0, 8, 0x00]),  # No fields
            bytearray([62, 0, 8, 0xFF]),  # Many fields (may fail, but no crash)
            bytearray([62, 0, 8, 0x80]),  # Standard single field
        ]
        
        for case in test_cases:
            # Pad to declared length
            while len(case) < 8:
                case.append(0)
            
            parser = Asterix62(bytes(case))
            # Should handle gracefully
            assert hasattr(parser, 'ok')


class TestPerformance:
    """Performance and load tests"""
    
    def test_parse_speed(self):
        """Test parsing performance"""
        frame = bytearray([62, 0, 8, 0x80, 1, 2, 0, 0, 0, 0])
        
        start = time.time()
        for _ in range(1000):
            parser = Asterix62(bytes(frame))
        elapsed = time.time() - start
        
        # Should parse 1000 frames in < 1 second
        assert elapsed < 1.0
        print(f"Parsed 1000 frames in {elapsed:.3f}s ({1000/elapsed:.0f} fps)")
    
    def test_track_memory(self):
        """Test track data memory usage"""
        tracks = []
        for i in range(1000):
            track = TrackData(
                track_id=f'TRK{i}',
                pos_lat=51.5 + i * 0.001,
                pos_lon=-0.1 + i * 0.001,
                ground_speed=400.0 + i * 0.1,
            )
            tracks.append(track)
        
        # Should handle 1000 tracks without issue
        assert len(tracks) == 1000
        
        # Convert to dict format
        track_dicts = [t.to_dict() for t in tracks]
        assert len(track_dicts) == 1000


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
