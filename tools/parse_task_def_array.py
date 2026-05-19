"""Parse the static task-def array discovered at file 0x2741DC.

Each entry is suspected to be 24 bytes:
  +0x00  uint32_t entry_fn   (Thumb +1)
  +0x04  const char *name
  +0x08  uint32_t stack_size (in words)
  +0x0C  uint32_t ?          (reserved / param ptr)
  +0x10  uint32_t priority
  +0x14  uint32_t ?

Walk the array until we hit non-pointer data.

Usage: python parse_task_def_array.py <fw.bin>
"""
import lzma, struct, sys

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

def runtime(off): return BASE + off
def file_off(addr):
    if addr < BASE: return None
    o = addr - BASE
    if o >= len(fw): return None
    return o

def read_str(addr, maxlen=64):
    fo = file_off(addr)
    if fo is None: return None
    end = fo
    while end < len(fw) and end < fo + maxlen and 0x20 <= fw[end] <= 0x7E:
        end += 1
    if end == fo: return None
    if end >= len(fw) or fw[end] != 0: return None
    return fw[fo:end].decode('ascii', errors='replace')

# Walk through entries of varying strides to find the one that works
print("=== Trying various strides to find the right structure ===\n")

# Search up to 60 entries
for stride in (20, 24, 28, 32):
    print(f"--- Stride {stride} ---")
    off = 0x2741DC
    valid_count = 0
    sample_output = []
    while off + stride <= len(fw):
        e = struct.unpack(f"<{stride // 4}I", fw[off:off+stride])
        # First word should be a code pointer (0x080xxxxx range, Thumb)
        fn = e[0]
        name_ptr = e[1] if stride >= 8 else 0
        if not (0x08000000 <= fn < 0x08800000):
            break
        if not (0x08000000 <= name_ptr < 0x08800000):
            break
        name = read_str(name_ptr)
        if name is None:
            break
        valid_count += 1
        rest = ", ".join(f"{w:#x}" for w in e[2:])
        sample_output.append(f"  +{off-0x2741DC:#04x}  fn={fn:#010x}  name={name_ptr:#010x} {name!r}  [{rest}]")
        off += stride
    print(f"  Valid entries: {valid_count}")
    for l in sample_output[:15]:
        print(l)
    print()
