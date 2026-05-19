"""Parse the RACE dispatch table and identify all command ranges.

Usage: python parse_dispatch.py <Maxwell_v1.0.1.74_XBOX_headset.bin>
"""
import lzma, struct, sys

if len(sys.argv) < 2:
    print("Usage: python parse_dispatch.py <Maxwell_v1.0.1.74_XBOX_headset.bin>")
    sys.exit(1)
FW = sys.argv[1]
with open(FW, "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

# Dispatch table starts at file 0x26B8E0
table_start = 0x26B8E0
entries = []
off = table_start
while off + 8 <= len(fw):
    cmd_min = struct.unpack("<H", fw[off:off+2])[0]
    cmd_max = struct.unpack("<H", fw[off+2:off+4])[0]
    handler = struct.unpack("<I", fw[off+4:off+8])[0]
    # End-of-table heuristic: cmd_min == 0 and cmd_max == 0 and handler == 0
    if cmd_min == 0 and cmd_max == 0 and handler == 0:
        break
    # Sanity check: handler should be in code region
    if not (0x08000000 <= handler < 0x08800000):
        break
    # cmd_max should be >= cmd_min
    if cmd_max < cmd_min:
        break
    entries.append((cmd_min, cmd_max, handler))
    off += 8

# Add known purposes
KNOWN = {
    0x0301: "RACE_READ_SDK_VERSION",
    0x0402: "RACE_STORAGE_PAGE_PROGRAM",
    0x0403: "RACE_STORAGE_PAGE_READ",
    0x0404: "RACE_STORAGE_PARTITION_ERASE",
    0x0900: "Source state / chatmix dispatcher (FUN_0015C91C, sub-commands 0x00-0x2F+)",
    0x0CC0: "RACE_GET_LINK_KEY",
    0x0CD5: "RACE_GET_BD_ADDRESS",
    0x1680: "RACE_READ_ADDRESS (RAM read 4 bytes)",
    0x1681: "RACE_WRITE_ADDRESS (RAM write — ERNW notes; not yet verified on Maxwell)",
    0x1C00: "RACE_FOTA_PARTITION_INFO_QUERY",
    0x1C01: "RACE_FOTA_INTEGRITY_CHECK",
    0x1C02: "RACE_FOTA_COMMIT",
    0x1C03: "RACE_FOTA_STOP",
    0x1C06: "RACE_FOTA_WRITE_STATE",
    0x1C08: "RACE_FOTA_START",
    0x1C0A: "RACE_FOTA_START_TRANSACTION",
    0x1E08: "RACE_GET_BUILD_VERSION",
    0x2C09: "Chatmix get / param read (per audeze-tray RE)",
    0x2C82: "Param write (audio config, 0x83 read counterpart)",
    0x2C83: "Param read (audio config, 0x82 write counterpart)",
}

print(f"=== RACE Command Dispatch Table (at file {table_start:#x}, runtime {BASE+table_start:#x}) ===")
print(f"Entries found: {len(entries)}\n")
print(f"{'cmd_min':>7s} {'cmd_max':>7s}  {'handler':>10s}  {'span':>5s}  Known purpose / commands in range")
print("-" * 110)
for cmd_min, cmd_max, handler in sorted(entries):
    span = cmd_max - cmd_min + 1
    # List known commands within this range
    known_str = ""
    knowns_in_range = []
    for k, v in KNOWN.items():
        if cmd_min <= k <= cmd_max:
            knowns_in_range.append(f"{k:#06x}={v}")
    if knowns_in_range:
        known_str = "; ".join(knowns_in_range)
    print(f"  {cmd_min:#06x}  {cmd_max:#06x}  {handler:#010x}  {span:5d}  {known_str if known_str else '(undocumented)'}")
