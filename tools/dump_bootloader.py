"""Dump the bootloader region of flash, 0x08000000 - 0x08013000."""
import struct, time, hid, sys

VID, PID = 0x3329, 0x4B1E
START = 0x08000000
END   = 0x08013000

def find_race_path():
    for d in hid.enumerate():
        if d['vendor_id']==VID and d['product_id']==PID and d['usage_page']==0xFF13:
            return d['path']

class R:
    def __init__(self):
        self.dev = hid.device(); self.dev.open_path(find_race_path())
        for _ in range(8):
            r = bytes(self.dev.get_input_report(7, 62))
            if struct.unpack("<H", r[1:3])[0] == 0: break
    def read_flash_page(self, addr):
        payload = bytes([0, 1]) + struct.pack("<I", addr)
        body = struct.pack("<BBHH", 0x05, 0x5A, 0x0008, 0x0403) + payload
        self.dev.write(b"\x06" + struct.pack("<H", len(body)) + body)
        deadline = time.time() + 1.0
        target_len = 6 + 8 + 256
        accum = b""
        while time.time() < deadline:
            r = bytes(self.dev.get_input_report(7, 62))
            length = struct.unpack("<H", r[1:3])[0]
            if length == 0:
                if len(accum) >= target_len: break
                if accum: break
                time.sleep(0.005); continue
            accum += r[3:3+length]
            if len(accum) >= target_len: break
        if len(accum) < 14: return None
        head, type_, rlen, rid = struct.unpack("<BBHH", accum[:6])
        if rid != 0x0403: return None
        return accum[14:14+256]
    def close(self):
        try: self.dev.close()
        except: pass

r = R()
out = bytearray()
total_pages = (END - START) // 0x100
t0 = time.time()
for i, addr in enumerate(range(START, END, 0x100)):
    page = r.read_flash_page(addr)
    if page is None or len(page) != 256:
        print(f"  page {i} @ {addr:#x} FAIL (got {len(page) if page else 0} bytes)")
        # Pad with zeros so offsets stay aligned
        out.extend(b"\x00" * 256)
        time.sleep(0.1)
        continue
    out.extend(page)
    if i % 16 == 0:
        elapsed = time.time() - t0
        print(f"  {i}/{total_pages} pages ({i*256/1024:.0f} KB)  {elapsed:.1f}s")
r.close()

outpath = r"C:\Users\Jona\Downloads\race-toolkit\maxwell_bootloader.bin"
with open(outpath, "wb") as f:
    f.write(out)
print(f"\nWrote {len(out)} bytes to {outpath}")
print(f"SHA256: ", end="")
import hashlib
print(hashlib.sha256(out).hexdigest())
