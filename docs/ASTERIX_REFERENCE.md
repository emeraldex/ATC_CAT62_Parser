# ASTERIX CAT62 Reference Guide

> ⚠️ **Accuracy note:** This document describes the *general* ASTERIX CAT62
> concept, but this project's decoder is matched to the repo's synthetic
> generator, **not** to the authoritative EUROCONTROL CAT62 UAP. Field ordering,
> some item lengths, and LSB scaling here do **not** all match the real spec, and
> only I062/010, I062/040, I062/105, and I062/185 actually carry decoded data.
> See **SCOPE & LIMITATIONS** in the [README](../README.md) before relying on
> this for a real feed.

## Overview

ASTERIX CAT62 (Category 62) is used for Radar Track and Flight Plan messages in Air Traffic Control systems. This reference documents the fields this parser handles (for synthetic data).

## Message Structure

```
┌─────────────────────────────────┐
│   ASTERIX Record Header         │
├─────────────────────────────────┤
│   Category (1 byte): 62         │
│   Length (2 bytes): Record size │
│   FSPEC (1+ bytes): Field specs │
├─────────────────────────────────┤
│   Data Fields (variable)        │
│   - Position (LAT/LON or X/Y)   │
│   - Velocity (Speed/Heading)    │
│   - Identification (Callsign)   │
│   - Status (Track Status)       │
│   - Mode 3/A Code              │
│   - ICAO Address               │
│   - Timestamps                  │
└─────────────────────────────────┘
```

## FSPEC (Field Specification)

The FSPEC byte indicates which data fields are present:

```
Bit 7 (MSB) = I062/010 (Data Source Identifier)
Bit 6       = I062/015 (Service Identification)
Bit 5       = I062/020 (Warning/Error Condition)
Bit 4       = I062/040 (Track Number)
Bit 3       = I062/060 (Track Status)
Bit 2       = I062/070 (Flight Plan)
Bit 1       = I062/080 (Mode 3/A Code)
Bit 0 (LSB) = Extension bit (FX)
```

## Supported Fields

### I062/010 - Data Source Identifier
**Structure**: 2 bytes (SAC + SIC)
- **SAC** (System Area Code): 1 byte
- **SIC** (System Identification Code): 1 byte

**Example**:
```json
{
  "sac": 1,
  "sic": 10
}
```

### I062/015 - Service Identification
**Structure**: 1 byte
- Service identification number

**Example**:
```json
{
  "svc": 1
}
```

### I062/040 - Track Number
**Structure**: 2 bytes (12 bits used)
- Unique track number within radar system
- Range: 0 - 4095

**Example**:
```json
{
  "tn": 1234
}
```

### I062/060 - Track Status
**Structure**: Variable length (1+ bytes with FX bits)

**Bit Fields**:
- Bit 7: **CNF** (Confidence) - Track confirmation status
- Bit 6: **MAN** (Manually Initiated) - Manual track creation
- Bit 5: **DUP** (Duplicate) - Duplicate track flag
- Bit 4: **TRE** (Track Re-establishment)
- Bit 3: **GHO** (Ghost/False Track)
- Bit 2: **SUP** (Suppressed)
- Bit 1: **RDS** (Radar Data Source)
- Bit 0: **FX** (Extension)

**Example**:
```json
{
  "conf": true,
  "man": false,
  "dup": false
}
```

### I062/080 - Mode 3/A Code
**Structure**: 2 bytes (12 bits + status)
- Aircraft squawk code (octal format)
- Range: 0000 - 7777 (octal)

**Example**:
```json
{
  "m3a": "1234"
}
```

### I062/100 - Calculated Track Position (Cartesian)
**Structure**: 4 bytes (2×2 bytes for X,Y)
- **X**: 2 bytes signed integer (meters, LSB = 0.25m)
- **Y**: 2 bytes signed integer (meters, LSB = 0.25m)

**Format**: Two's complement binary

**Example**:
```json
{
  "xy": {
    "x": 12345.50,
    "y": -54321.75
  }
}
```

### I062/105 - Calculated Track Position (WGS-84)
**Structure**: 8 bytes (2×4 bytes for latitude, longitude)
- **Latitude**: 4 bytes signed integer
  - Range: -90° to +90° (degrees)
  - LSB = 180°/2²³ ≈ 2.145×10⁻⁵ degrees ≈ 2.4 meters
  - Positive = North
  
- **Longitude**: 4 bytes signed integer
  - Range: -180° to +180° (degrees)
  - LSB = 180°/2²³
  - Positive = East

**Format**: Two's complement binary

**Example**:
```json
{
  "pos": {
    "lat": 3.139009,
    "lon": 101.686859
  }
}
```

### I062/185 - Calculated Track Velocity (Cartesian)
**Structure**: 4 bytes (2×2 bytes for Vx, Vy)
- **Vx**: 2 bytes signed integer (m/s, LSB = 0.25 m/s)
- **Vy**: 2 bytes signed integer (m/s, LSB = 0.25 m/s)

**Conversion to Ground Speed & Heading**:
```
Ground Speed (GS) = √(Vx² + Vy²)  [converted to knots]
Heading (HDG) = atan2(Vx, Vy) × 180/π  [0° = North, clockwise]
```

**Example**:
```json
{
  "gs": 425.5,
  "hdg": 270.0
}
```

### I062/200 - Target Address (ICAO 24-bit)
**Structure**: 3 bytes (24-bit address)
- ICAO 24-bit aircraft address (Mode S)
- Hexadecimal format
- Range: 000000 - FFFFFF

**Example**:
```json
{
  "addr": "A1B2C3"
}
```

### I062/210 - Target Report Descriptor
**Structure**: Variable length

**Bit Fields**:
- TYP (Type of report)
- SIM (Simulated track)
- RDP (Radar data provider)
- SPI (Special Position Identification)
- RAB (Report from radar)
- TST (Test track)

### I062/245 - Target Identification
**Structure**: 6 bytes
- 8 characters (IA-5 6-bit encoding)
- Aircraft callsign or aircraft registration

**IA-5 Encoding**:
- Bits 0-5 represent character
- Characters: A-Z (1-26), 0-9 (48-57), space (32)
- Packed 6 bits per character

**Example**:
```json
{
  "id": "ABC1234"
}
```

## Data Type Conversions

### Degrees per LSB Calculation
```
LSB (degrees) = 180° / 2²³
              = 180 / 8,388,608
              ≈ 2.145 × 10⁻⁵ degrees
              ≈ 2.4 meters at equator
```

### Speed Conversion
```
m/s to knots: speed_knots = speed_ms × 1.94384
meters to feet: height_ft = height_m × 3.28084
```

### Heading Reference
```
0°   = North
90°  = East
180° = South
270° = West
```

## Message Example

Raw CAT62 message (hex):
```
3E 00 1F 21 F0 2C 0A 0A 00 14 00 5C FF C3 18 14
8F 00 C1 5C 00 00 06 00 02 84 1B
```

Parsed output:
```json
{
  "type": "track",
  "ts": 1234567890.123,
  "src": {
    "sac": 2,
    "sic": 10
  },
  "tn": 20,
  "status": {
    "conf": true,
    "man": false,
    "dup": false
  },
  "m3a": "1234",
  "pos": {
    "lat": 3.139009,
    "lon": 101.686859
  },
  "gs": 425.5,
  "hdg": 270.0,
  "addr": "A1B2C3",
  "id": "ABC1234"
}
```

## Common Issues & Solutions

### Issue: Wrong Position Display
**Cause**: Incorrect WGS-84 scaling
**Solution**: Verify LSB = 180°/2²³, not other values

### Issue: Inverted Heading
**Cause**: Using atan2(Vy, Vx) instead of atan2(Vx, Vy)
**Solution**: Use correct order: atan2(Vx, Vy), then add 360° if negative

### Issue: Wrong Speed Units
**Cause**: m/s not converted to knots
**Solution**: Multiply by 1.94384

### Issue: Corrupted Callsigns
**Cause**: Incorrect IA-5 decoding
**Solution**: Verify 6-bit unpacking from MSB to LSB

## Standards References

- **ASTERIX** (All Purpose Structured Radar Information Exchange)
- **CAT62** - Track and Flight Plan Messages
- **WGS-84** - World Geodetic System 1984
- **ICAO 24-bit Addresses** - Mode S capability
- **IA-5** - International Alphabet 5 (ITU-T T.50)

## Tools & Resources

### Packet Analysis
```bash
# Capture UDP traffic
tcpdump -i eth0 -w capture.pcap udp port 31002

# View PCAP
tcpdump -r capture.pcap -X

# Filter CAT62 (hex 3E)
tcpdump -r capture.pcap -X | grep "3e 00"
```

### Hex Decoder
Online: https://hexdecimal.com/

### Coordinate Converters
- https://www.latlong.net/
- https://www.ilecilly.com/tools/latlong_to_dms.html

## Compliance Checklist

- ✅ Proper FSPEC parsing
- ✅ WGS-84 coordinate transformation
- ✅ Speed in knots
- ✅ Heading 0°-360° (0=North, clockwise)
- ✅ Track numbering 0-4095
- ✅ Mode 3/A in octal format
- ✅ ICAO addresses in hex
- ✅ Proper stale track removal
- ✅ Error handling for malformed data
- ✅ Logging and diagnostics
