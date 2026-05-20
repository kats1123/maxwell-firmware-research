"""Compare firmware versions by NVDM-key usage, not by byte offset.

Recompiled firmware shifts every address, so byte diffs are useless.
Instead: in each version, find every `movw rN, #<key>` for the
balance/source NVDM keys, and show context. If an older version has
MORE writers of 0xF702 (source state), the per-source feature was
wired up and later disabled.

Usage: python compare_versions.py <fw1> <fw2> [fw3...]
"""
import lzma, struct, sys, capstone, re

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

KEYS = {
    0xF702: "source state",
    0xF700: "source state sibling",
    0xF665: "USB-C balance",
    0xF668: "wireless balance",
}
# the NVDM read/write helpers — but addresses shift per version, so we
# detect them by "movw rN,#key then a BL within a few instrs"
NVDM_WRITE_NAMES = ()

def decomp(path):
    with open(path, "rb") as f:
        raw = f.read()
    payload = raw[0x1000:]
    fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
    return lzma.decompress(fixed, format=lzma.FORMAT_ALONE)

def find_movw(fw, imm):
    """Find all movw rN,#imm — scan 2-byte aligned, disassemble."""
    hits = []
    for off in range(0, len(fw) - 4, 2):
        chunk = fw[off:off+4]
        try:
            ins = next(md.disasm(chunk, off))
        except StopIteration:
            continue
        if ins.mnemonic.lower() != "movw":
            continue
        op = ins.op_str.lower().replace(" ", "")
        m = re.match(r"(r\d+),#(0x[0-9a-f]+|\d+)$", op)
        if m and int(m.group(2), 0) == imm:
            hits.append((off, m.group(1)))
    return hits

def classify(fw, off, reg):
    """Look at the ~12 instrs after a movw to guess read vs write.
    A following BL is the nvdm call; we can't resolve names across
    versions, but we can see if r1/r2 are set up like a write (str
    before the BL) vs read."""
    chunk = fw[off:off+0x30]
    insns = list(md.disasm(chunk, off))
    seq = []
    for ins in insns[:12]:
        seq.append(ins.mnemonic.lower())
    return " ".join(seq[:8])

paths = sys.argv[1:]
versions = []
for p in paths:
    name = p.split("\\")[-1]
    versions.append((name, decomp(p)))

for key, desc in KEYS.items():
    print(f"\n{'='*70}")
    print(f"=== NVDM key {key:#06x}  ({desc})")
    print(f"{'='*70}")
    for name, fw in versions:
        hits = find_movw(fw, key)
        print(f"\n  {name}: {len(hits)} movw site(s)")
        for off, reg in hits:
            seq = classify(fw, off, reg)
            print(f"    file {off:#08x}  movw {reg},#{key:#06x}   [{seq}]")
