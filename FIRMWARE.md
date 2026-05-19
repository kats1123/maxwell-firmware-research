# Firmware Format & Architecture

## TL;DR

- Firmware files are LZMA-Alone compressed (NO encryption)
- Decompresses to a 3.2 MB ARM Cortex-M4 image
- Header at offset 0–0x21A contains version info, partition table, and a SHA-256 hash of the rest of the file
- Decompressing it is trivial; modifying and re-flashing is **blocked by bootloader signature verification** (see [FLASHING.md](FLASHING.md))

## File layout

A raw `Maxwell_v1.0.1.NN_XBOX_headset.bin` file is structured as:

```
0x0000-0x001F : 32-byte SHA-256 hash of bytes[0x100:]
0x0020-0x00FF : 0xFF padding
0x0100-0x011B : version metadata (TLV format, contains "v1.0.1.NN" string)
0x012C-0x0166 : partition table TLV (tag 0x0012, 4 entries of 12 bytes each)
0x01F0-0x021A : chip identifiers ("AB1568_Headset", "headset_ref_design")
0x021B-0x0FFF : 0xFF padding
0x1000+       : LZMA-Alone stream
```

### Partition table

At offset `0x130` (TLV with tag `0x0012`, length `0x34`):

```
04 00 00 00                    count = 4 entries

Entry 1: 00 10 00 00  00 40 11 00  00 30 01 00
         addr=0x1000  size=0x114000  ??=0x13000
Entry 2: 00 50 11 00  00 b0 19 00  00 30 13 00
         addr=0x115000 size=0x19b000 ??=0x133000
Entry 3: 00 00 2b 00  00 40 01 00  00 c0 32 00
         addr=0x2b0000 size=0x14000  ??=0x32c000
Entry 4: 00 40 2c 00  00 b0 04 00  00 00 45 00
         addr=0x2c4000 size=0x4b000  ??=0x450000
```

Partition `addr` and `size` cover the decompressed firmware exactly:
0x114000 + 0x19b000 + 0x14000 + 0x4b000 = **0x30E000 = 3,203,072 bytes** (= the full decompressed firmware).

The third field in each entry is unclear — possibly a per-partition CRC, offset
in compressed stream, or a flash bank address. Not needed for decompression.

## LZMA-Alone parameters

The LZMA stream at offset `0x1000` uses:

| Field | Value |
|-------|-------|
| Properties byte | `0x5D` → `lc=3, lp=0, pb=2` (standard) |
| Dictionary size | `0x4000` = 16 KB (small — chip is RAM-constrained) |
| Decompressed size | `0x30E000` = 3,203,072 bytes (explicit, NOT 0xFFFF...) |

Python's `lzma` module is strict about the size field — it rejects streams with
explicit size. The actual Maxwell decoder is more lenient. To decompress with
Python you have to patch the size to `0xFFFFFFFFFFFFFFFF` first.

## Decompression

```python
import lzma, struct

with open("Maxwell_v1.0.1.74_XBOX_headset.bin", "rb") as f:
    raw = f.read()

payload = raw[0x1000:]
# Patch size field to streaming mode so Python lzma accepts it
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
decompressed = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)

# decompressed is now 3,203,072 bytes - the raw ARM Cortex-M4 firmware
with open("fw_decompressed.bin", "wb") as f:
    f.write(decompressed)
```

Alternatively, use [airoha-firmware-parser](https://github.com/ramikg/airoha-firmware-parser):

```
python airoha_decrypt.py --no-decrypt --from Maxwell_v1.0.1.74_XBOX_headset.bin --to fw.dec.bin
```

## Architecture

| Property | Value |
|----------|-------|
| **SoC** | Airoha AB1568 (= MediaTek MT2822) |
| **CPU** | ARM Cortex-M4F |
| **SDK** | Airoha IoT_SDK_for_BT_Audio v3.4.1 |
| **Code load base** | `0x08000000` (standard Cortex-M flash region) |
| **SRAM region** | `0x14000000` |
| **Audio HW regs** | `0x42000000`–`0x4200FFFF` (78 distinct addresses used) |
| **Total code** | ~3.2 MB decompressed |

### Memory map (best-known)

```
0x08000000+    ROM/Flash, code           (~2.1 MB used)
0x14000000+    SRAM (data, audio mixer)
0x14201xxx     Per-stream gain config structs
0x14229E58     Audio context base (gain bytes, channel selectors)
0x14230Bxx     Factory EQ defaults (RAM mirror)
0x4200xxxx     Audio hardware registers (memory-mapped I/O)
```

### Audio hardware register heat map

| Block | Inferred purpose | Distinct regs | Total refs |
|-------|------------------|--------------:|-----------:|
| `0x42000xxx` | Audio TOP / clock + main FIFO | 15 | **102** |
| `0x42001xxx` | DAC / output stage | 6 | 6 |
| `0x42002xxx` | I2S external interface | 6 | 12 |
| `0x42003xxx` | Audio FIFO 2 / DMA | 2 | 2 |
| `0x42004xxx` | Codec interconnect | 8 | **53** |
| `0x42005xxx` | Echo cancellation (mic) | 9 | 9 |
| `0x42006xxx` | DSP coefficient memory | 6 | 11 |
| `0x42007xxx` | PEQ filter bank 2 | 1 | 2 |
| `0x42008xxx` | Power management | 2 | 3 |
| `0x4200Axxx` | Bluetooth audio interface | 3 | 3 |
| `0x4200Cxxx` | LE Audio (LC3) | 2 | 2 |
| `0x4200Dxxx` | Game/Chat USB endpoint | 1 | 1 |
| `0x4200Exxx` | Audio routing / DAC tuning | 4 | 9 |
| `0x4200Fxxx` | DMA descriptors / buffer pointers | 13 | 21 |

> **Note**: block labels are *inferred* from address patterns and reference
> frequency. AB1568 datasheet is not public. Most-referenced registers
> (`0x420008E0` with 78 refs, `0x42004136` with 43 refs) are almost certainly
> the main audio FIFO write/read ports.

### Strings in decompressed firmware

Some useful debug strings in the decompressed payload:

| Offset | String | What it tells us |
|--------|--------|------------------|
| ~0x267E10 | `IoT_SDK_for_BT_Audio_V3.4.1.AB1565_AB1568` | SDK version |
| ~0x272495 | `Audeze Maxwell XBOX Headset` | Product USB descriptor |
| ~0x2724C0 | `Audeze Maxwell Chat` | Chat USB audio device name |
| ~0x2724D8 | `Audeze Maxwell Game` | Game USB audio device name |
| ~0x26988X | `AT+EAUDIO=VOL_STREAM_2A2D` | Volume command for regs 0x2A-0x2D |
| ~0x26993X | `AT+EAUDIO=VOL_STREAM_6A6D` | Volume command for regs 0x6A-0x6D |
| ~0x26DD9X | `gain_value_mapping` + `audio_nvdm` | NVDM key names for gain LUT |
| ~0xED8FC | `clk_skew_compensate_by_sw_algorithm` | Clock drift compensation |

## Tools used

- **Ghidra** 12.0.4 — disassembly and decompilation, ARM Cortex-M little-endian
- **Python lzma** module — decompression
- **airoha-firmware-parser** — drop-in decompression script
