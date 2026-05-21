"""Generic Thumb-2 disassembler for the decompressed Maxwell firmware.

Usage: python disasm.py <fw.bin> <start_addr> <end_addr> [--data]

Resolves literal-pool loads (ldr rX,[pc,#imm]) to their target value,
and annotates BL targets. With --data, also prints word values.
"""
import lzma, struct, sys, capstone

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
md.detail = True

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000
fo = lambda a: a - BASE

start = int(sys.argv[2], 0)
end = int(sys.argv[3], 0)
show_data = "--data" in sys.argv

chunk = fw[fo(start):fo(end)]

def word_at(addr):
    o = fo(addr)
    if 0 <= o <= len(fw) - 4:
        return struct.unpack("<I", fw[o:o+4])[0]
    return None

for ins in md.disasm(chunk, start):
    mn = ins.mnemonic.lower()
    op = ins.op_str
    note = ""
    # literal pool resolution
    if mn.startswith("ldr") and "[pc," in op:
        # pc is aligned(addr+4)
        try:
            imm = int(op.split("#")[-1].rstrip("]"), 0)
            pc = (ins.address + 4) & ~3
            litaddr = pc + imm
            val = word_at(litaddr)
            if val is not None:
                note = f"   ; ={val:#010x} (@ {litaddr:#x})"
        except Exception:
            pass
    elif mn in ("bl", "blx", "b", "b.w", "bl.w", "bne", "beq", "bne.w", "beq.w",
                "bls", "bls.w", "bhi", "bcc", "bcs", "bge", "blt", "bgt", "ble",
                "cbz", "cbnz"):
        note = "   ; ->"
    print(f"  {ins.address:#010x}  {ins.bytes.hex():12s}  {mn:9s} {op}{note}")
