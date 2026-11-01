#!/usr/bin/env python3
"""
extract_idr.py - Chỉ trích xuất IDR frame từ file MP4 hỏng.
Mỗi IDR frame ghi ra một file: Header + IDR frame  ->  IDR_0001.h264, IDR_0002.h264, ...

Cách dùng:
    python extract_idr.py broken.mp4 Header Log_video.txt Log_audio.txt [thu_muc_ra]

Một IDR frame gồm:
    - slice đầu: cụm có nhãn "IDR frame" trong log (vd 25 88 82 00)
    - các slice tiếp theo: cụm có nhãn "IDR slice" (vd 25 00 3F C8, 25 00 1F E2, ...)
Frame kết thúc khi gặp NAL video không thuộc IDR, hoặc khi gặp slice đầu của frame IDR mới.

Cấu trúc mỗi file:
    [Header] 25 88 82 00 ...data
    00 00 01 25 00 3F C8 ...data
    00 00 01 25 00 1F E2 ...data
    00 00 01 25 00 0B F4 ...data
"""
import os
import re
import sys
from collections import Counter

START_CODE = b"\x00\x00\x01"
LOG_RE = re.compile(r"\[((?:[0-9A-Fa-f]{2}\s?){4})\]\s+(\d+)\s*(.*)")

BAD_SEQ = re.compile(rb"\x00\x00[\x00-\x02]")      # không thể có trong NAL hợp lệ
ASCII_RUN = re.compile(rb"[\x20-\x7E]{24,}")       # rác ASCII dài


# ---------------------------------------------------------------- log
def load_log(path):
    """Đọc log -> list (sig bytes, nhãn loại)."""
    items = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LOG_RE.search(line)
            if m:
                sig = bytes.fromhex(m.group(1).replace(" ", ""))
                items.append((sig, m.group(3).strip()))
    return items


def find_events(data, video_sigs, audio_sigs):
    """List (vị trí, 'V'|'A', sig) đã sắp xếp, quét cả match chồng lấn."""
    pats = {s: "V" for s in video_sigs}
    for s in audio_sigs:
        pats.setdefault(s, "A")                    # trùng thì ưu tiên video
    rx = re.compile(
        b"(?=(" + b"|".join(re.escape(p) for p in pats) + b"))", re.DOTALL
    )
    return [(m.start(), pats[m.group(1)], m.group(1)) for m in rx.finditer(data)]


# ---------------------------------------------------------------- lọc rác
def base_trim(seg, size):
    """Cắt theo độ dài AVCC và chuỗi cấm 00 00 00/01/02."""
    if 0 < size <= len(seg):
        seg = seg[:size]
    m = BAD_SEQ.search(seg, 1)
    if m:
        seg = seg[:m.start()]
    return seg


def learn_markers(raw, n=8, tail=256, min_ratio=0.3):
    """Học marker rác lặp ở đuôi nhiều đoạn (rác riêng của từng file)."""
    cnt = Counter()
    for seg, size in raw:
        s = base_trim(seg, size)[-tail:]
        cnt.update({s[i:i + n] for i in range(len(s) - n + 1)})
    need = max(5, int(len(raw) * min_ratio))
    return [p for p, c in cnt.items() if c >= need and len(set(p)) > 2]


def trim_segment(seg, size, markers, use_ascii=True):
    seg = base_trim(seg, size)
    cut = len(seg)
    for mk in markers:
        i = seg.find(mk, 1, cut)
        if i != -1:
            cut = i
    if use_ascii:
        m = ASCII_RUN.search(seg, 4, cut)
        if m:
            cut = m.start()
    return seg[:cut]


# ---------------------------------------------------------------- chính
def extract_idr(mp4_path, header_path, vlog, alog, out_dir=".", use_ascii=True):
    vitems = load_log(vlog)
    aitems = load_log(alog)
    video_sigs = {s for s, _ in vitems}
    audio_sigs = {s for s, _ in aitems}

    # Phân loại cụm IDR theo nhãn trong log (NAL type 5 = byte đầu & 0x1F == 5)
    idr_sigs = {s for s, lb in vitems if "IDR" in lb.upper() and (s[0] & 0x1F) == 5}
    start_sigs = {s for s, lb in vitems if "IDR FRAME" in lb.upper() and s in idr_sigs}
    print(f"IDR signatures: {len(idr_sigs)} (slice đầu: {len(start_sigs)})")
    if not idr_sigs:
        print("Log video không có cụm IDR nào.")
        return

    with open(mp4_path, "rb") as f:
        data = f.read()
    with open(header_path, "rb") as f:
        header = f.read()
    header_has_start = header.endswith(START_CODE)

    events = find_events(data, video_sigs, audio_sigs)
    print(f"Tìm thấy {len(events)} mốc")

    # Bước 1: gom đoạn thô của TẤT CẢ NAL video (để học rác cho chính xác)
    raw_all = []          # (seg, size)
    index = {}            # chỉ số event -> chỉ số trong raw_all
    for i, (pos, kind, sig) in enumerate(events):
        if kind != "V":
            continue
        nxt = events[i + 1][0] if i + 1 < len(events) else len(data)
        size = int.from_bytes(data[pos - 4:pos], "big") if pos >= 4 else 0
        index[i] = len(raw_all)
        raw_all.append((data[pos:nxt], size))

    markers = learn_markers(raw_all)
    print(f"Học được {len(markers)} marker rác")

    # Bước 2: gom các slice IDR thành frame
    frames, cur, cur_sigs = [], [], set()

    def close():
        nonlocal cur, cur_sigs
        if cur:
            frames.append(cur)
        cur, cur_sigs = [], set()

    for i, (pos, kind, sig) in enumerate(events):
        if kind != "V":
            continue                       # audio xen giữa: bỏ qua, không cắt frame
        if sig not in idr_sigs:
            close()                        # gặp NAL video khác -> hết frame IDR
            continue
        seg, size = raw_all[index[i]]
        nal = trim_segment(seg, size, markers, use_ascii)
        if len(nal) < 4 or (nal[0] & 0x1F) != 5:
            continue
        # bắt đầu frame mới: slice đầu (theo log) hoặc sig đã có trong frame hiện tại
        new_frame = (sig in start_sigs) if start_sigs else (sig in cur_sigs)
        if new_frame:
            close()
        cur.append(nal)
        cur_sigs.add(sig)
    close()

    # Bước 3: ghi mỗi frame ra một file
    os.makedirs(out_dir, exist_ok=True)
    for k, nals in enumerate(frames, 1):
        path = os.path.join(out_dir, f"IDR_{k:04d}.h264")
        with open(path, "wb") as out:
            out.write(header)
            for j, nal in enumerate(nals):
                if not (j == 0 and header_has_start):
                    out.write(START_CODE)
                out.write(nal)
        print(f"  {path}: {len(nals)} NAL, {sum(map(len, nals))} byte")
    print(f"Đã ghi {len(frames)} IDR frame vào '{out_dir}'")


if __name__ == "__main__":
    args = sys.argv[1:]
    use_ascii = True
    if "--no-ascii" in args:
        args.remove("--no-ascii")
        use_ascii = False
    if len(args) not in (4, 5):
        print(__doc__)
        sys.exit(1)
    extract_idr(*args, use_ascii=use_ascii)
