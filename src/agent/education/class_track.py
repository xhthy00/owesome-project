"""班级选科方向：只用于学科横向对比分轨，不改达线/均分/排名口径。"""

from __future__ import annotations

from collections import Counter
from typing import Any

TRACK_PHY = "物理方向"
TRACK_HIS = "历史方向"
TRACK_ORDER = (TRACK_PHY, TRACK_HIS)


def track_of_xkkm(xkkm: Any) -> str:
    t = str(xkkm or "").strip()
    if t.startswith("物") or t in ("物理类", "物理方向", "理科"):
        return TRACK_PHY
    if t.startswith("史") or t.startswith("历") or t in ("历史类", "历史方向", "文科"):
        return TRACK_HIS
    return ""


def class_name_of(row: dict[str, Any]) -> str:
    return str(row.get("class_name") or row.get("class") or row.get("bj") or "").strip()


def _row_track(row: dict[str, Any]) -> str:
    for key in ("track", "xkkm", "xk"):
        hit = track_of_xkkm(row.get(key))
        if hit:
            return hit
    return ""


def class_track_map(score_rows: list[dict[str, Any]] | None) -> dict[str, str]:
    """班级 → 方向：该班学生 xkkm/track 多数票。票数不足则不标。"""
    votes: dict[str, Counter[str]] = {}
    for row in score_rows or []:
        if not isinstance(row, dict):
            continue
        cls = class_name_of(row)
        track = _row_track(row)
        if not cls or not track:
            continue
        votes.setdefault(cls, Counter())[track] += 1
    out: dict[str, str] = {}
    for cls, ctr in votes.items():
        track, n = ctr.most_common(1)[0]
        if n > 0:
            out[cls] = track
    return out


def both_tracks_present(class_tracks: dict[str, str]) -> bool:
    found = {t for t in class_tracks.values() if t in TRACK_ORDER}
    return found == set(TRACK_ORDER)


def filter_rows_by_track(
    rows: list[dict[str, Any]],
    class_tracks: dict[str, str],
    track: str,
    *,
    class_of=class_name_of,
) -> list[dict[str, Any]]:
    return [r for r in rows if class_of(r) and class_tracks.get(class_of(r)) == track]


__all__ = [
    "TRACK_HIS",
    "TRACK_ORDER",
    "TRACK_PHY",
    "both_tracks_present",
    "class_name_of",
    "class_track_map",
    "filter_rows_by_track",
    "track_of_xkkm",
]
