"""Read the NVDM default values registered for 0xF665 / 0xF668 in one or more
Maxwell firmware images. Version/address independent: locates the
`movw r0,#<key>` instruction and decodes the `movw r3,#<value>` that
precedes it (the value handed to nvdm_set_default).

Usage: python read_nvdm_defaults.py <fw1.bin> [fw2.bin ...]
"""
import lzma, struct, sys

def decompress(path):
    raw = open(path, "rb").read()
    payload = raw[0x1000:]
    fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
    return lzma.decompress(fixed, format=lzma.FORMAT_ALONE)

def movw_key_pattern(key):
    """4-byte little-endian encoding of `movw r0,#key`."""
    imm4 = (key >> 12) & 0xF; i = (key >> 11) & 1
    imm3 = (key >> 8) & 7;    imm8 = key & 0xFF
    hw1 = 0xF240 | (i << 10) | imm4
    hw2 = (imm3 << 12) | (0 << 8) | imm8     # rd = 0
    return struct.pack("<HH", hw1, hw2)

def decode_movw(b):
    """Decode a 4-byte buffer as Thumb-2 movw; return (rd, imm16) or None."""
    if len(b) < 4:
        return None
    hw1, hw2 = struct.unpack("<HH", b[:4])
    if (hw1 & 0xFBF0) != 0xF240:
        return None
    if hw2 & 0x8000:
        return None
    imm4 = hw1 & 0xF; i = (hw1 >> 10) & 1
    imm3 = (hw2 >> 12) & 7; rd = (hw2 >> 8) & 0xF; imm8 = hw2 & 0xFF
    return rd, (imm4 << 12) | (i << 11) | (imm3 << 8) | imm8

for path in sys.argv[1:]:
    name = path.replace("\\", "/").split("/")[-1]
    try:
        fw = decompress(path)
    except Exception as e:
        print(f"{name}: decompress failed ({e})")
        continue
    print(f"{name}:")
    for key in (0xF665, 0xF668):
        o = fw.find(movw_key_pattern(key))
        if o < 0:
            print(f"   {key:#06x}: movw site not found")
            continue
        val = None
        # value movw is normally 8 bytes before; search a small window for rd=3
        for back in (8, 6, 10, 4, 12, 14, 16, 18):
            v = decode_movw(fw[o - back:o - back + 4])
            if v and v[0] == 3:
                val = v[1]; break
        if val is None:
            print(f"   {key:#06x}: value movw not located near {o:#x}")
        else:
            ch0, ch1 = val & 0xFF, (val >> 8) & 0xFF
            print(f"   {key:#06x} default = {val:#06x}  ->  ch0={ch0}  ch1={ch1}")
