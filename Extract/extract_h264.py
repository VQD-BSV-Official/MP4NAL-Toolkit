#!/usr/bin/env python3
"""
Trích xuất H.264 từ file MP4 hỏng bằng các cụm 4 byte đặc trưng trong log.

Cách dùng:
    python extract_h264.py broken.mp4 Header Log_video.txt Log_audio.txt video.h264
"""
import re
import sys

START_CODE = b"\x00\x00\x01"
LOG_RE = re.compile(r"\[((?:[0-9A-Fa-f]{2}\s?){4})\]\s+(\d+)")


def load_signatures(path):
    """Đọc log -> set các cụm 4 byte (bytes)."""
    sigs = set()
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LOG_RE.search(line)
            if m:
                sigs.add(bytes.fromhex(m.group(1).replace(" ", "")))
    return sigs


def find_events(data, video_sigs, audio_sigs):
    """Trả về list (vị trí, 'V'|'A') đã sắp xếp, quét cả các match chồng lấn."""
    pats = {s: "V" for s in video_sigs}
    for s in audio_sigs:
        pats.setdefault(s, "A")          # trùng với video thì ưu tiên video

    rx = re.compile(
        b"(?=(" + b"|".join(re.escape(p) for p in pats) + b"))", re.DOTALL
    )
    return [(m.start(), pats[m.group(1)]) for m in rx.finditer(data)]


def extract(mp4_path, header_path, vlog, alog, out_path):
    video_sigs = load_signatures(vlog)
    audio_sigs = load_signatures(alog)
    print(f"Video signatures: {len(video_sigs)}, Audio signatures: {len(audio_sigs)}")

    with open(mp4_path, "rb") as f:
        data = f.read()
    with open(header_path, "rb") as f:
        header = f.read()

    # Nếu Header đã kết thúc bằng start code thì NAL đầu nối thẳng vào sau nó
    header_has_start = header.endswith(START_CODE)

    events = find_events(data, video_sigs, audio_sigs)
    print(f"Tìm thấy {len(events)} mốc")

    nal_count = 0

    with open(out_path, "wb") as out:
        out.write(header)               # chỉ ghi Header 1 lần duy nhất

        for i, (pos, kind) in enumerate(events):
            if kind != "V":
                continue

            nxt_pos, nxt_kind = (events[i + 1] if i + 1 < len(events)
                                 else (len(data), "END"))
            seg = data[pos:nxt_pos]
            if len(seg) < 4:
                continue

            # 4 byte độ dài (AVCC) đứng ngay trước NAL: dùng để cắt cho đúng
            size = int.from_bytes(data[pos - 4:pos], "big") if pos >= 4 else 0
            if size == len(seg) or size == len(seg) - 4:
                seg = seg[:size]
            elif nxt_kind == "V":
                seg = seg[:-4]          # bỏ 4 byte length của NAL kế tiếp

            # NAL đầu tiên: Header + 25 88 82 00 ... (không thêm start code)
            # Các NAL sau: 00 00 01 + NAL
            if not (nal_count == 0 and header_has_start):
                out.write(START_CODE)
            out.write(seg)
            nal_count += 1

    print(f"Đã ghi {nal_count} NAL vào {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(1)
    extract(*sys.argv[1:])