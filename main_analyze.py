"""Phân tích H.264 / H.265 / AAC trực tiếp trong MP4, không cần ffmpeg và
không ghi file trung gian. Chỉ đọc bảng chỉ mục (moov) rồi seek tới từng sample.

Cần đặt cùng thư mục với Analyze_H264.py, Analyze_H265.py, Analyze_AAC.py
(tái sử dụng hàm classify() của các file đó).
"""
import os
import struct
import sys
from collections import Counter
from contextlib import redirect_stdout

from codec.h264 import classify as classify_h264
from codec.h265 import classify as classify_h265
from codec.aac import classify as classify_aac


CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl"}
H264_CODECS = {"avc1", "avc3"}
H265_CODECS = {"hvc1", "hev1"}
AAC_CODECS = {"mp4a"}
PCM_CODECS = {"sowt", "twos", "lpcm", "ipcm", "in24", "in32", "fl32", "fl64"}
LOG_FILE = "Log_Analyze.txt"


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for x in self.streams:
            x.write(s)

    def flush(self):
        for x in self.streams:
            x.flush()


# ---------------------------------------------------------------- parse box
def iter_boxes(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size, typ = struct.unpack(">I4s", hdr)
        hsize = 8
        if size == 1:                       # 64-bit size
            size = struct.unpack(">Q", f.read(8))[0]
            hsize = 16
        elif size == 0:                     # box kéo dài đến hết file
            size = end - pos
        if size < hsize:
            break
        yield typ.decode("latin1"), pos + hsize, pos + size
        pos += size


def walk(f, start, end, tracks, cur):
    for typ, s, e in iter_boxes(f, start, end):
        if typ == "trak":
            t = {}
            tracks.append(t)
            walk(f, s, e, tracks, t)
        elif typ in CONTAINERS:
            walk(f, s, e, tracks, cur)
        elif cur is not None:
            parse_leaf(f, typ, s, e, cur)


def parse_leaf(f, typ, s, e, t):
    if typ == "hdlr":
        f.seek(s + 8)
        t["handler"] = f.read(4).decode("latin1")
        return
    if typ not in ("stsd", "stsz", "stsc", "stco", "co64", "stss"):
        return

    f.seek(s)
    d = f.read(e - s)

    if typ == "stsd":
        esize, codec = struct.unpack(">I4s", d[8:16])
        t["codec"] = codec.decode("latin1")
        body = d[8:8 + esize]
        i = body.find(b"avcC")
        if i >= 0:                          # lengthSizeMinusOne ở byte 4
            t["nal_len_size"] = (body[i + 4 + 4] & 3) + 1
        i = body.find(b"hvcC")
        if i >= 0:                          # lengthSizeMinusOne ở byte 21
            t["nal_len_size"] = (body[i + 4 + 21] & 3) + 1
    elif typ == "stsz":
        fixed, count = struct.unpack(">II", d[4:12])
        if fixed:
            t["stsz"] = [fixed] * count
        else:
            t["stsz"] = list(struct.unpack(f">{count}I", d[12:12 + 4 * count]))
    elif typ == "stsc":
        count = struct.unpack(">I", d[4:8])[0]
        t["stsc"] = [struct.unpack(">3I", d[8 + 12 * i: 20 + 12 * i]) for i in range(count)]
    elif typ == "stco":
        count = struct.unpack(">I", d[4:8])[0]
        t["stco"] = list(struct.unpack(f">{count}I", d[8:8 + 4 * count]))
    elif typ == "co64":
        count = struct.unpack(">I", d[4:8])[0]
        t["stco"] = list(struct.unpack(f">{count}Q", d[8:8 + 8 * count]))
    elif typ == "stss":
        count = struct.unpack(">I", d[4:8])[0]
        t["stss"] = count


def parse_mp4(f):
    f.seek(0, os.SEEK_END)
    size = f.tell()
    tracks = []
    for typ, s, e in iter_boxes(f, 0, size):
        if typ == "moov":
            walk(f, s, e, tracks, None)
    return tracks


# ------------------------------------------------------------------ samples
def sample_offsets(t):
    """Sinh (chỉ_số, offset, kích_thước) của từng sample trong track."""
    sizes, chunks, stsc = t["stsz"], t["stco"], t["stsc"]
    idx, j = 0, 0
    for ci, chunk_off in enumerate(chunks, 1):
        while j + 1 < len(stsc) and stsc[j + 1][0] <= ci:
            j += 1
        off = chunk_off
        for _ in range(stsc[j][1]):
            if idx >= len(sizes):
                return
            yield idx, off, sizes[idx]
            off += sizes[idx]
            idx += 1


def analyze_video(f, t, classify):
    n = t.get("nal_len_size", 4)
    counter, frame_counter = Counter(), Counter()
    for _, off, size in sample_offsets(t):
        pos, end = off, off + size
        while pos + n <= end:
            f.seek(pos)
            b = f.read(n + 4)
            nal_len = int.from_bytes(b[:n], "big")
            chunk = b[n:n + min(4, nal_len)]
            if chunk:
                label, first = classify(chunk)
                counter[(chunk.hex(" ").upper(), label)] += 1
                if first:
                    frame_counter[label] += 1
            pos += n + nal_len
    return counter, frame_counter


def analyze_aac(f, t):
    counter, frame_counter = Counter(), Counter()
    for _, off, size in sample_offsets(t):
        f.seek(off)
        chunk = f.read(min(4, size))
        if len(chunk) == 4:
            label = classify_aac(chunk)
            counter[(chunk.hex(" ").upper(), label)] += 1
            frame_counter[label] += 1
    return counter, frame_counter


# -------------------------------------------------------------------- main
def report(counter, frame_counter, unit):
    print(f"  Tổng số {unit}: {sum(counter.values())}")
    print(f"  Số cụm 4 byte khác nhau: {len(counter)}\n")
    print(f"  {'4 byte đầu':<14}{'Số lần':<10}Loại")
    print("  " + "-" * 55)
    for (chunk, label), n in counter.most_common():
        print(f"  [{chunk}]  {n:<10}{label}")
    print("\n  Thống kê số frame:")
    for label, n in frame_counter.most_common():
        print(f"    {label:<34}{n}")


def main():
    src = input("Nhập tên file mp4 (vd: REF.mp4): ").strip().strip('"')
    if not os.path.isfile(src):
        print(f"Không tìm thấy file: {src}")
        return

    with open(src, "rb") as f:
        tracks = parse_mp4(f)
        if not tracks:
            print("Không tìm thấy track nào (thiếu moov?).")
            return

        for i, t in enumerate(tracks):
            codec = t.get("codec", "?")
            print(f"\n=== Track {i}: {t.get('handler', '?')} / {codec} "
                  f"({len(t.get('stsz', []))} sample"
                  + (f", {t['stss']} keyframe" if "stss" in t else "") + ") ===")

            if "stsz" not in t or "stco" not in t or "stsc" not in t:
                print("  Thiếu bảng sample (có thể là fragmented MP4 - cần đọc moof/trun).")
                continue

            if codec in H264_CODECS:
                report(*analyze_video(f, t, classify_h264), "NAL")
            elif codec in H265_CODECS:
                report(*analyze_video(f, t, classify_h265), "NAL")
            elif codec in AAC_CODECS:
                report(*analyze_aac(f, t), "frame AAC")
            elif codec in PCM_CODECS:
                print("  Track PCM sẵn trong MP4: có thể đọc thẳng dữ liệu tại các offset sample.")
            else:
                print("  Codec chưa hỗ trợ.")


if __name__ == "__main__":
    with open(LOG_FILE, "w", encoding="utf-8") as log, \
            redirect_stdout(Tee(sys.stdout, log)):
        main()
