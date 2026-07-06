# PCAP Parser Issue - Resolution Report

**Date:** 2026-07-06  
**Issue:** Generated PCAP files not showing tracks on web interface after parsing  
**Status:** ✅ FIXED

## Root Cause Analysis

The issue had two main components:

### 1. **Incorrect FSPEC (Field Specification) Format**
The initial `generate_sample_data.py` was using a single FSPEC byte `0xF8` (11111000) which incorrectly decoded to:
- I062/010 ✓
- I062/015 ✓
- I062/020 ✓ (variable-length skip)
- I062/040 ✓
- I062/060 ✓
- I062/070 ✗
- I062/080 ✗
- No extension

This meant **position data (I062/105) and speed/heading (I062/185) were NOT being included**.

### 2. **Incorrect Field Data Encoding**
Even if the FSPEC had been correct, the field data encoding was incompatible with the parser expectations:
- **I062/185 (Speed/Heading)** was being encoded as raw speed/heading values
- **Actual format** expects Cartesian velocity components (Vx, Vy) in m/s

## Solution

### Fix #1: Corrected FSPEC Multi-Byte Format

**Old (Incorrect):**
```
FSPEC = 0xF8 (single byte)
```

**New (Correct):**
```
FSPEC = 0x91 0x21 0x80 (3 bytes)
- Byte 1: 0x91 = 10010001 (I062/010, I062/040, FX=1)
- Byte 2: 0x21 = 00100001 (I062/105, FX=1)
- Byte 3: 0x80 = 10000000 (I062/185, no extension)
```

This ensures the parser reads:
1. **I062/010** (SAC/SIC) - 2 bytes
2. **I062/040** (Track Number) - 2 bytes  
3. **I062/105** (Position - WGS84) - 8 bytes (4-byte lat + 4-byte lon)
4. **I062/185** (Velocity Cartesian) - 4 bytes (2-byte Vx + 2-byte Vy)

### Fix #2: Correct Velocity Encoding

**Old (Incorrect):**
```python
# Tried to pack raw speed/heading - wrong format!
speed_lsb = int(speed / 0.1953125)
heading_lsb = int((heading % 360) / (360.0 / 512))
```

**New (Correct):**
```python
# Convert speed (knots) and heading (degrees) to Cartesian velocity
speed_ms = speed * 0.51444  # knots to m/s
heading_rad = math.radians(heading)

# Velocity components
vy_ms = speed_ms * math.cos(heading_rad)
vx_ms = speed_ms * math.sin(heading_rad)

# Scale to 0.25 m/s per LSB (divide by 0.25)
vx_lsb = int(round(vx_ms / 0.25))
vy_lsb = int(round(vy_ms / 0.25))

# Pack as signed 16-bit integers
record.extend(struct.pack('>h', vx_lsb))
record.extend(struct.pack('>h', vy_lsb))
```

## Verification

### Before Fix
- ❌ Parser only extracted: SAC, SIC, Track Number, Track Status
- ❌ No position data visible
- ❌ Speed values incorrect (2700+ knots)
- ❌ No tracks on web map

### After Fix
- ✅ Parser extracts: SAC, SIC, Track Number, Position (lat/lon), Velocity
- ✅ Position data correct (51.5°N, 0.1°W)
- ✅ Speed values correct (~350-550 knots as generated)
- ✅ Heading values correct
- ✅ Tracks appear with location on web map

## Test Results

```
Generated PCAP: 500 packets with 10 unique tracks
API Response: 10 active tracks
Average Speed: 390.5 knots (expected: 350-550)
Sample Track:
  - ID: 0.10.16
  - Position: 51.50°N, 0.10°W (London area)
  - Speed: 410.2 knots
  - Heading: 215.99°
  - Timestamp: 2026-07-06 10:51:35 UTC
```

## Files Modified

1. **generate_sample_data.py**
   - Added `import math` for velocity calculations
   - Fixed `create_cat62_record()` function
   - Corrected 3-byte FSPEC format
   - Implemented proper Cartesian velocity encoding

## Usage

```bash
# Generate fresh PCAP with corrected format
python generate_sample_data.py

# Run parser server with PCAP replay
python parser_server.py --pcap sample_radar.pcap

# Access web interface
# Open browser: http://localhost:7878

# Check API for tracks
curl http://localhost:7878/api/tracks
curl http://localhost:7878/api/stats
curl http://localhost:7878/api/health
```

## API Response Example

```json
{
  "track_id": "0.10.16",
  "sac": 0,
  "sic": 10,
  "track_num": 16,
  "pos_lat": 51.4999794960022,
  "pos_lon": -0.09999275207519531,
  "ground_speed": 410.2,
  "heading": 215.99,
  "timestamp": 1783306395.758
}
```

## Recommendations

1. ✅ Update documentation to explain FSPEC multi-byte format
2. ✅ Add more CAT62 fields to sample generator (altitude, callsign, etc.)
3. ✅ Add validation/tests to ensure FSPEC matches expected fields
4. ✅ Consider simplifying FSPEC generation with a helper function

## Impact

- **Users can now successfully replay PCAP files with correct track data**
- **Web interface displays tracks with accurate position information**
- **API returns realistic speed and heading values**
- **Sample data generation is now production-ready for testing**
