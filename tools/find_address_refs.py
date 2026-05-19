"""Find all references to a 32-bit address in the firmware.

For each occurrence:
- If it's at a 4-byte-aligned offset, it's likely a literal-pool entry.
- We also search via movw+movt instruction pair pattern, which on Cortex-M
  is how large immediates are commonly built.

Usage: python find_address_refs.py <fw.bin> <hex_addr>
Example: python find_address_refs.py fw.bin 0x142039AC
"""
import lzma, struct, sys
import capstone

if len(sys.argv) < 3:
    print("Usage: python find_address_refs.py <fw.bin> <hex_addr>")
    sys.exit(1)

target = int(sys.argv[2], 16)

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

print(f"Searching for references to {target:#010x} in {len(fw):,}-byte firmware...\n")

# Literal pool references (4-byte aligned)
target_bytes = struct.pack("<I", target)
lit_refs = []
start = 0
while True:
    idx = fw.find(target_bytes, start)
    if idx == -1: break
    if idx % 4 == 0:
        lit_refs.append(idx)
    start = idx + 1

print(f"=== Literal-pool refs (aligned uint32 in firmware) === {len(lit_refs)} found")
for r in lit_refs:
    print(f"  file {r:#x} (runtime {BASE+r:#x})")

# Also look at +1 form (Thumb mode bit set)
target_bytes_t = struct.pack("<I", target | 1)
start = 0
lit_refs_t = []
while True:
    idx = fw.find(target_bytes_t, start)
    if idx == -1: break
    if idx % 4 == 0:
        lit_refs_t.append(idx)
    start = idx + 1
if lit_refs_t:
    print(f"\n=== Thumb+1 literal-pool refs === {len(lit_refs_t)} found")
    for r in lit_refs_t:
        print(f"  file {r:#x} (runtime {BASE+r:#x})")

# movw/movt pair search:
# movw rN, #(target & 0xffff)  — encoding: F2 4X XX YY  (variable)
# movt rN, #(target >> 16)     — encoding: F2 CX XX YY
lo = target & 0xFFFF
hi = (target >> 16) & 0xFFFF

# Scan 2-byte aligned for movw + movt to same register
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
md.detail = False

print(f"\n=== movw+movt pairs (lo={lo:#06x}, hi={hi:#06x}) ===")
pair_refs = []
movw_state = {}  # reg -> (addr, value)
for off in range(0, len(fw) - 4, 2):
    chunk = fw[off:off+4]
    try:
        insns = list(md.disasm(chunk, BASE + off))
    except:
        continue
    if not insns: continue
    ins = insns[0]
    op = ins.op_str.lower().replace(" ", "")
    mn = ins.mnemonic.lower()
    if mn == "movw":
        # format: "rN,#0xXXXX"
        try:
            reg, val = op.split(",")
            val = int(val.lstrip("#"), 0)
            movw_state[reg] = (off, val)
        except: pass
    elif mn == "movt":
        try:
            reg, val = op.split(",")
            val = int(val.lstrip("#"), 0)
            if reg in movw_state:
                prev_off, prev_val = movw_state[reg]
                combined = (val << 16) | prev_val
                if combined == target:
                    pair_refs.append((prev_off, off, reg))
                    print(f"  movw {reg},#{prev_val:#06x} @ file {prev_off:#x} -> movt @ file {off:#x}  (combined = {target:#x})")
        except: pass

print(f"\nTotal movw+movt pairs building {target:#x}: {len(pair_refs)}")
