"""Chọn DUY NHẤT 1 header (SPS+PPS) tốt nhất trong data.py cho IDR_0001.h264 (bản nhanh).

Tăng tốc so với bản cũ:
  1. Bỏ trùng header giống hệt nhau.
  2. Đọc độ phân giải từ SPS bằng Python (không tốn tiến trình) -> bỏ ngay các
     header khác độ phân giải mong muốn (TARGET_RES) trước khi gọi ffmpeg.
  3. Chỉ 1 tiến trình ffmpeg / header (bỏ ffprobe); đưa dữ liệu qua pipe, không ghi file tạm.
     Kích thước lấy từ header PPM của chính ảnh xuất ra.
  4. ffmpeg -threads 1 (đã chạy song song nhiều header nên không cần đa luồng trong từng cái).
  5. Chấm điểm trên cột lấy mẫu (mỗi 4 cột) -> median nhanh hơn ~4 lần.
Cần: ffmpeg, numpy.   Chạy: python try_headers_fast.py
"""
import os, sys, subprocess
import numpy as np
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from header_data import FILES                       # dict: tên -> bytes header (SPS+PPS)

IDR = open(os.path.join(BASE, "IDR_0001.h264"), "rb").read()
TARGET_RES = (1920, 1080)                    # None = không lọc theo độ phân giải
COL_STEP = 4


# ---------- đọc độ phân giải từ SPS ----------
class Bits:
    def __init__(self, data): self.d, self.p = data, 0
    def u(self, n=1):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v
    def ue(self):
        z = 0
        while self.u() == 0: z += 1
        return (1 << z) - 1 + (self.u(z) if z else 0)
    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def sps_resolution(header):
    """Trả về (w, h) hoặc None nếu không đọc được (khi đó vẫn giữ lại để thử)."""
    try:
        i = 0
        while True:
            i = header.index(b"\x00\x00\x01", i) + 3
            if header[i] & 0x1F == 7:
                break
        j = header.find(b"\x00\x00\x01", i)
        nal = header[i + 1: j if j >= 0 else len(header)].rstrip(b"\x00")
        b = Bits(nal.replace(b"\x00\x00\x03", b"\x00\x00"))
        prof = b.u(8); b.u(8); b.u(8); b.ue()
        cf = 1
        if prof in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
            cf = b.ue()
            if cf == 3: b.u(1)
            b.ue(); b.ue(); b.u(1)
            if b.u(1):
                for k in range(8 if cf != 3 else 12):
                    if b.u(1):
                        last = nxt = 8
                        for _ in range(16 if k < 6 else 64):
                            if nxt: nxt = (last + b.se()) % 256
                            last = nxt or last
        b.ue()
        pt = b.ue()
        if pt == 0: b.ue()
        elif pt == 1:
            b.u(1); b.se(); b.se()
            for _ in range(b.ue()): b.se()
        b.ue(); b.u(1)
        mw = b.ue() + 1; mh = b.ue() + 1
        fmo = b.u(1)
        if not fmo: b.u(1)
        b.u(1)
        cl = cr = ct = cb = 0
        if b.u(1): cl, cr, ct, cb = b.ue(), b.ue(), b.ue(), b.ue()
        cux = 1 if cf == 0 else (1 if cf == 3 else 2)
        cuy = (1 if cf in (0, 3) else 2) * (2 - fmo)
        return mw * 16 - cux * (cl + cr), (2 - fmo) * mh * 16 - cuy * (ct + cb)
    except Exception:
        return None


# ---------- giải mã + chấm điểm ----------
def score(item):
    name, header = item
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-err_detect", "explode", "-threads", "1",
         "-f", "h264", "-i", "pipe:0", "-frames:v", "1",
         "-pix_fmt", "rgb24", "-f", "image2pipe", "-c:v", "ppm", "pipe:1"],
        input=header + IDR, capture_output=True)
    out = r.stdout
    if r.returncode != 0 or r.stderr.strip() or not out.startswith(b"P6"):
        return None
    try:
        _, dims, _, rest = out.split(b"\n", 3)
        w, h = map(int, dims.split())
        img = np.frombuffer(rest, np.uint8)[: w * h * 3].reshape(h, w, 3)[:, ::COL_STEP]
    except Exception:
        return None
    img = img.astype(np.int16)
    bands = np.abs(np.median(img[1:] - img[:-1], axis=1)).max(axis=1)
    return float(bands.max()), float(np.sort(bands)[-3:].sum()), name


if __name__ == "__main__":
    seen, todo = set(), []
    for name, hd in FILES.items():
        if hd in seen: continue
        seen.add(hd)
        res = sps_resolution(hd)
        if TARGET_RES and res and res != TARGET_RES: continue
        todo.append((name, hd))

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        ok = [r for r in ex.map(score, todo) if r]
    if not ok:
        sys.exit("Không có header nào giải mã được.")

    best = min(ok)[2]
    print("Header tốt nhất:", best)

    out = os.path.join(BASE, "best_header.h264")
    with open(out, "wb") as f:
        f.write(FILES[best] + IDR)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", out, "-frames:v", "1",
                    os.path.join(BASE, "best_frame.png")])
    print("Đã lưu: best_header.h264, best_frame.png")
