"""
Audeze Maxwell firmware patcher v5 — v1.0.1.74 only, with TLV 0x0014 fix.

Key difference from v4: recomputes the per-partition SHA-256 hashes in
TLV 0x0014. The bootloader verifies firmware integrity by computing
SHA-256 over each partition's decompressed bytes and comparing with the
hash stored at TLV 0x0014. Without updating those hashes, the bootloader
rejects the firmware at "Transferring".

Patches:
  1. BT/dongle balance — change factory default for NVDM key 0xf668
  2. USB-C cable balance — change factory default for NVDM key 0xf665
  3. Concurrent playback — NOP the router-reset BL

After flashing, perform a factory reset to apply new defaults.
"""

import argparse
import hashlib
import lzma
import struct
import sys


def encode_movw(rd, imm16):
    """Encode Thumb-2 MOVW Rd, #imm16. Returns 4 bytes (little-endian)."""
    imm4 = (imm16 >> 12) & 0xF
    i    = (imm16 >> 11) & 1
    imm3 = (imm16 >> 8) & 0x7
    imm8 = imm16 & 0xFF
    hw1 = 0xF000 | (i << 10) | 0x0240 | imm4
    hw2 = (imm3 << 12) | ((rd & 0xF) << 8) | imm8
    return struct.pack("<HH", hw1, hw2)


# v1.0.1.74 patch offsets (file offsets in DECOMPRESSED firmware)
PROFILE = {
    "bt_offset":          0x186C72,
    "bt_original_imm16":  0x9393,   # movw r3, #0x9393 (L=147, R=147)
    "usb_offset":         0x186CA4,
    "usb_original_imm16": 0x958D,   # movw r3, #0x958D (L=141, R=149)
    # Concurrent playback patch site 1: in source-dispatch function, fixes USB-C+BT
    "concurrent_offset_1":  0x135C66,
    "concurrent_original_1": bytes([0x02, 0xF0, 0x6F, 0xF9]),  # bl FUN_00137F48 (router_reset)
    # Concurrent playback patch site 2: in state-dispatch function, fixes BT+dongle
    # State 1 and 0xF1 both call bigger_router_reset (FUN_00137F9C). The BL is at this offset.
    "concurrent_offset_2":  0x135CC4,
    "concurrent_original_2": bytes([0x02, 0xF0, 0x6A, 0xF9]),  # bl FUN_00137F9C (bigger_router_reset)
    # .data section bytes for 0x142039AC (runtime balance buffer initial value).
    # Reset handler memcpys from flash 0x082A6F48 (file offset 0x287F48) to SRAM
    # 0x142039AC at every true cold boot. Patching these bytes gives boot-time
    # firmware-only L/R balance — no host intervention needed. (May 2026)
    "data_offset":          0x287F48,
    "data_original":        bytes([0x88, 0x88, 0x00, 0x00]),  # L=136, R=136, slider=0, dir=0
}


def build_data_patch(usb_l, usb_r):
    """Patch for the .data section initial bytes of 0x142039AC."""
    return {
        "name": "Runtime balance .data init (0x142039AC initial value)",
        "file_offset": PROFILE["data_offset"],
        "original_bytes": PROFILE["data_original"],
        "patched_bytes":  bytes([usb_l & 0xFF, usb_r & 0xFF, 0x00, 0x00]),
        "explanation": "Boot-time SRAM init for 0x142039AC: L=%d, R=%d (was: L=136, R=136). "
                       "Combined with SRAM retention, this gives firmware-only balance that survives "
                       "all normal use until next true cold reset." % (usb_l, usb_r),
    }


def build_patches(usb_l, usb_r, bt_l, bt_r, include_concurrent=True, include_data_patch=True):
    patches = []
    patches.append({
        "name": "BT/dongle balance (NVDM 0xf668 default)",
        "file_offset": PROFILE["bt_offset"],
        "original_bytes": encode_movw(3, PROFILE["bt_original_imm16"]),
        "patched_bytes":  encode_movw(3, (bt_r << 8) | bt_l),
        "explanation": "BT/dongle default: L=%d, R=%d (was: L=147, R=147)" % (bt_l, bt_r),
    })
    patches.append({
        "name": "USB-C cable balance (NVDM 0xf665 default)",
        "file_offset": PROFILE["usb_offset"],
        "original_bytes": encode_movw(3, PROFILE["usb_original_imm16"]),
        "patched_bytes":  encode_movw(3, (usb_r << 8) | usb_l),
        "explanation": "USB-C default: L=%d, R=%d (was: L=141, R=149)" % (usb_l, usb_r),
    })
    if include_data_patch:
        patches.append(build_data_patch(usb_l, usb_r))
    if include_concurrent:
        patches.append({
            "name": "Concurrent playback site 1 (USB-C+BT)",
            "file_offset": PROFILE["concurrent_offset_1"],
            "original_bytes": PROFILE["concurrent_original_1"],
            "patched_bytes":  bytes([0x00, 0xBF, 0x00, 0xBF]),
            "explanation": "Skip router-reset BL FUN_00137F48 in source-dispatch fn (USB-C+BT case)",
        })
        patches.append({
            "name": "Concurrent playback site 2 (BT+dongle)",
            "file_offset": PROFILE["concurrent_offset_2"],
            "original_bytes": PROFILE["concurrent_original_2"],
            "patched_bytes":  bytes([0x00, 0xBF, 0x00, 0xBF]),
            "explanation": "Skip bigger_router_reset BL FUN_00137F9C in state-dispatch fn (BT+dongle/state-0xF1 case)",
        })
    return patches


def apply_patch(data, patch):
    off = patch["file_offset"]
    expected = patch["original_bytes"]
    actual = bytes(data[off:off + len(expected)])
    if actual != expected:
        print(f"  ERROR: bytes at {off:#x} don't match.")
        print(f"    Expected: {expected.hex()}")
        print(f"    Actual:   {actual.hex()}")
        return False
    data[off:off + len(patch["patched_bytes"])] = patch["patched_bytes"]
    print(f"  Patched {off:#x}: {expected.hex()} -> {patch['patched_bytes'].hex()}")
    return True


def parse_partition_sizes(header):
    """TLV 0x0012 at offset 0x12e: count(4) + N * (src(4) + size(4) + dst(4))."""
    pt_offset = 0x12e
    tag = struct.unpack("<H", header[pt_offset:pt_offset+2])[0]
    if tag != 0x0012:
        raise ValueError(f"Expected partition table TLV 0x0012 at {pt_offset:#x}, got {tag:#x}")
    count = struct.unpack("<I", header[pt_offset+4:pt_offset+8])[0]
    sizes = []
    for i in range(count):
        e = header[pt_offset+8 + i*12 : pt_offset+8 + (i+1)*12]
        sizes.append(struct.unpack("<I", e[4:8])[0])
    return sizes


def find_tlv_0014(header):
    """Find TLV 0x0014 (per-partition SHA-256 hashes) in header. Returns (offset, count, length)."""
    off = 0x100
    while off < 0x1000:
        if header[off] == 0xFF:
            off += 1; continue
        if off + 4 > 0x1000:
            break
        tag = struct.unpack("<H", header[off:off+2])[0]
        length = struct.unpack("<H", header[off+2:off+4])[0]
        if tag == 0x0014:
            count = struct.unpack("<I", header[off+4:off+8])[0]
            return (off, count, length)
        off += 4 + length
    raise ValueError("TLV 0x0014 (per-partition hashes) not found")


def find_tlv_0011(header):
    """Find TLV 0x0011 (basic info, contains LZMA stream size). Returns offset of TLV header."""
    off = 0x100
    while off < 0x1000:
        if header[off] == 0xFF:
            off += 1; continue
        if off + 4 > 0x1000:
            break
        tag = struct.unpack("<H", header[off:off+2])[0]
        length = struct.unpack("<H", header[off+2:off+4])[0]
        if tag == 0x0011:
            return off
        off += 4 + length
    raise ValueError("TLV 0x0011 (basic info) not found")


def update_lzma_stream_size(header_bytes, new_lzma_size):
    """TLV 0x0011 value bytes 6-9 = LZMA stream size (LE u32). Update it."""
    hdr = bytearray(header_bytes)
    tlv_off = find_tlv_0011(hdr)
    # TLV layout: tag(2) + length(2) + value
    # Value bytes [0..1]: 01 01 (flags)
    # Value bytes [2..5]: 00 10 00 00 = LE 0x1000 (LZMA start offset)
    # Value bytes [6..9]: ac 40 1f 00 = LE LZMA stream size
    size_off = tlv_off + 4 + 6
    old_size = struct.unpack("<I", hdr[size_off:size_off+4])[0]
    hdr[size_off:size_off+4] = struct.pack("<I", new_lzma_size)
    print(f"  TLV 0x0011 LZMA size: {old_size:#x} -> {new_lzma_size:#x}")
    return bytes(hdr)


def decompress_firmware(raw):
    header = raw[:0x1000]
    payload = raw[0x1000:]
    fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
    decompressed = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
    return header, decompressed


def compress_firmware(header, decompressed):
    filters = [{
        "id": lzma.FILTER_LZMA1,
        "dict_size": 16384,  # 16 KB — matches Audeze original
        "lc": 3, "lp": 0, "pb": 2,
    }]
    compressed = lzma.compress(decompressed, format=lzma.FORMAT_ALONE, filters=filters)
    # Patch the LZMA-alone header to include the explicit decompressed size
    out = bytearray(compressed)
    out[5:13] = struct.pack("<Q", len(decompressed))
    return header + bytes(out)


def update_partition_hashes(header_bytes, decompressed, partition_sizes):
    """Recompute SHA-256 of each partition slice and write to TLV 0x0014."""
    hdr = bytearray(header_bytes)
    tlv_off, count, length = find_tlv_0014(hdr)
    assert count == len(partition_sizes), f"TLV 0x0014 count={count} but partition table has {len(partition_sizes)}"
    print(f"  Updating TLV 0x0014 @ {tlv_off:#x} (count={count})")
    file_offset = 0
    for i, sz in enumerate(partition_sizes):
        chunk = decompressed[file_offset : file_offset + sz]
        new_h = hashlib.sha256(chunk).digest()
        slot = tlv_off + 8 + i*32
        old_h = bytes(hdr[slot:slot+32])
        if new_h != old_h:
            hdr[slot:slot+32] = new_h
            print(f"    P{i}: hash changed  {old_h[:8].hex()}... -> {new_h[:8].hex()}...")
        else:
            print(f"    P{i}: unchanged  {new_h[:8].hex()}...")
        file_offset += sz
    return bytes(hdr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="input", required=True)
    ap.add_argument("--out", dest="output", required=True)
    ap.add_argument("--usb-l", type=int, default=141)
    ap.add_argument("--usb-r", type=int, default=143, help="USB-C right channel (default 143, factory 149)")
    ap.add_argument("--bt-l",  type=int, default=141)
    ap.add_argument("--bt-r",  type=int, default=154, help="BT/dongle right channel (default 154, factory 147)")
    ap.add_argument("--no-concurrent", action="store_true")
    ap.add_argument("--no-data-patch", action="store_true",
                    help="Skip the .data init patch (only patches NVDM defaults — for users who want HQ to control runtime values)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="Skip patching, just verify input firmware integrity")
    args = ap.parse_args()

    with open(args.input, "rb") as f:
        raw = f.read()
    print(f"Loaded {len(raw)} bytes from {args.input}")
    print(f"  outer SHA-256 match: {hashlib.sha256(raw[0x100:]).hexdigest() == raw[:32].hex()}")

    header, decompressed = decompress_firmware(raw)
    print(f"  decompressed: {len(decompressed):#x} bytes")

    partition_sizes = parse_partition_sizes(header)
    print(f"  partitions: {[hex(s) for s in partition_sizes]}")

    # Verify input integrity
    tlv_off, count, length = find_tlv_0014(header)
    file_offset = 0
    print(f"  TLV 0x0014 verify:")
    for i, sz in enumerate(partition_sizes):
        h = hashlib.sha256(decompressed[file_offset:file_offset+sz]).digest()
        expected = bytes(header[tlv_off+8+i*32 : tlv_off+8+(i+1)*32])
        print(f"    P{i}: {h.hex()[:16]}...  {'OK' if h == expected else 'MISMATCH'}")
        file_offset += sz

    if args.verify_only:
        return

    print(f"\nConfiguration:")
    print(f"  USB-C cable: L={args.usb_l}, R={args.usb_r} (delta R-L = {args.usb_r-args.usb_l:+d})")
    print(f"  BT/dongle:   L={args.bt_l}, R={args.bt_r} (delta R-L = {args.bt_r-args.bt_l:+d})")
    print(f"  Concurrent playback: {'OFF' if args.no_concurrent else 'ON'}")

    patches = build_patches(args.usb_l, args.usb_r, args.bt_l, args.bt_r,
                            include_concurrent=not args.no_concurrent,
                            include_data_patch=not args.no_data_patch)
    data = bytearray(decompressed)
    for patch in patches:
        print(f"\nApplying: {patch['name']}")
        print(f"  {patch['explanation']}")
        if not apply_patch(data, patch):
            sys.exit(1)

    if args.dry_run:
        print("\nDry run — no output written")
        return

    # 1) Update per-partition hashes in TLV 0x0014 (key fix vs v4)
    print(f"\nUpdating per-partition hashes (TLV 0x0014)...")
    header_updated = update_partition_hashes(header, bytes(data), partition_sizes)

    # 2) Re-compress
    print(f"\nRe-compressing...")
    out_raw = compress_firmware(header_updated, bytes(data))
    new_lzma_size = len(out_raw) - 0x1000
    print(f"  size: {len(out_raw)} bytes (orig {len(raw)})  LZMA stream: {new_lzma_size:#x} bytes")

    # 2b) Update TLV 0x0011 with new LZMA stream size, then re-emit
    print(f"\nUpdating LZMA stream size (TLV 0x0011)...")
    header_final = update_lzma_stream_size(header_updated, new_lzma_size)
    out_raw = header_final + out_raw[0x1000:]

    # 3) Recompute outer SHA-256 over file[0x100:]
    out_bytes = bytearray(out_raw)
    new_hash = hashlib.sha256(bytes(out_bytes[0x100:])).digest()
    out_bytes[0:32] = new_hash
    print(f"  outer SHA-256: {new_hash.hex()}")

    with open(args.output, "wb") as f:
        f.write(bytes(out_bytes))
    print(f"\nWrote patched firmware to {args.output}")

    # Final verification
    print(f"\nVerifying output integrity...")
    h2, d2 = decompress_firmware(bytes(out_bytes))
    sizes2 = parse_partition_sizes(h2)
    tlv_off2, _, _ = find_tlv_0014(h2)
    file_offset = 0
    all_ok = True
    for i, sz in enumerate(sizes2):
        h = hashlib.sha256(d2[file_offset:file_offset+sz]).digest()
        expected = bytes(h2[tlv_off2+8+i*32 : tlv_off2+8+(i+1)*32])
        ok = h == expected
        all_ok = all_ok and ok
        print(f"  P{i}: {'OK' if ok else 'MISMATCH'}")
        file_offset += sz
    outer_ok = hashlib.sha256(bytes(out_bytes[0x100:])).digest() == bytes(out_bytes[0:32])
    print(f"  outer SHA: {'OK' if outer_ok else 'MISMATCH'}")
    print(f"  ALL CHECKS: {'PASS' if all_ok and outer_ok else 'FAIL'}")


if __name__ == "__main__":
    main()
