#!/usr/bin/env python3
"""Test CAT62 API endpoints"""
import urllib.request
import json
import time

time.sleep(1)

try:
    # Try to get tracks
    print("Checking /api/tracks...")
    response = urllib.request.urlopen('http://localhost:7878/api/tracks', timeout=5)
    tracks = json.loads(response.read())
    print(f"Tracks returned: {len(tracks)}")
    if tracks:
        print(f"First track: {tracks[0]}")
    
    # Try to get stats
    print("\nChecking /api/stats...")
    response = urllib.request.urlopen('http://localhost:7878/api/stats', timeout=5)
    stats = json.loads(response.read())
    print(f"Stats: {stats}")
    
    # Try to get health
    print("\nChecking /api/health...")
    response = urllib.request.urlopen('http://localhost:7878/api/health', timeout=5)
    health = json.loads(response.read())
    print(f"Health: {health}")
    
except Exception as e:
    print(f"Error: {e}")
