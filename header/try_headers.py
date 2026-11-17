"""Chọn DUY NHẤT 1 header (SPS+PPS) tốt nhất trong data.py cho IDR_0001.h264.

Cách chọn:
  1. Ghép header + IDR rồi giải mã bằng ffmpeg; header nào lỗi/không ra hình thì loại.
  2. Ảnh hỏng do sai header thường bị "lệch dải" (các slice lệch sáng/màu so với
     slice kế bên). Đo bằng độ lệch trung vị của cả hàng pixel giữa 2 dòng liền kề:
     ảnh đúng ~0, ảnh hỏng có dải lệch lớn.
  3. Chọn header có điểm lệch thấp nhất.
Cần: ffmpeg, ffprobe, numpy.   Chạy: python try_headers.py
"""
import os, sys, subprocess, tempfile
import numpy as np
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from header_data import FILES                      # dict: tên -> bytes header (SPS+PPS)

IDR_PATH = os.path.join(BASE, "IDR_0001.h264")
IDR = open(IDR_PATH, "rb").read()


def run(cmd):
    return subprocess.run(cmd, capture_output=True)


def score(item):
    name, header = item
    with tempfile.NamedTemporaryFile(suffix=".h264", delete=False) as f:
        f.write(header + IDR)
        tmp = f.name
    try:
        p = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", tmp])
        try:
            w, h = map(int, p.stdout.decode().strip().split(",")[:2])
        except Exception:
            return None
        if not (16 <= w <= 8192 and 16 <= h <= 8192):
            return None
        r = run(["ffmpeg", "-v", "error", "-err_detect", "explode", "-i", tmp,
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        if r.returncode != 0 or r.stderr.strip() or len(r.stdout) != w * h * 3:
            return None
        img = np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3).astype(np.int16)
        d = np.median(img[1:] - img[:-1], axis=1)       # (h-1, 3)
        bands = np.abs(d).max(axis=1)
        return (float(bands.max()), float(np.sort(bands)[-3:].sum()), name)
    finally:
        os.remove(tmp)


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        ok = [r for r in ex.map(score, FILES.items()) if r]
    if not ok:
        sys.exit("Không có header nào giải mã được.")

    ok.sort()                                  # điểm lệch nhỏ nhất đứng đầu
    best = ok[0][2]
    print("Header tốt nhất:", best)

    out = os.path.join(BASE, "best_header.h264")
    with open(out, "wb") as f:
        f.write(FILES[best] + IDR)             # header + IDR -> file phát được
    run(["ffmpeg", "-v", "error", "-y", "-i", out, "-frames:v", "1",
         os.path.join(BASE, "best_frame.png")])
    print("Đã lưu: best_header.h264, best_frame.png")
