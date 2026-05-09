from typing import Iterable

_HARDCODED_ALIAS_MAP: dict[str, str] = {
    "中出": "中出し",
    "内射": "中出し",
    "中出射精": "中出し",
    "單體作品": "単体作品",
    "単體作品": "単体作品",
    "デジモ": "數位馬賽克",
    "スレンダー": "苗條",
    "苗条": "苗條",
    "3P・4P": "多P",
    "3P": "多P",
    "4P": "多P",
    "キス・接吻": "口交",
    "接吻": "口交",
    "キス": "口交",
    "高画質": "高畫質",
    "ハイビジョン": "高畫質",
    "独占配信": "DMM獨家",
    "中文字幕版": "中文字幕",
}

_STOPWORDS: frozenset[str] = frozenset({
    "単体作品",
    "高畫質",
    "DMM獨家",
    "數位馬賽克",
    "薄馬賽克",
    "中文字幕",
    "4K",
    "偶像藝人",
    "DVD多士爐",
    "高解析度",
    "ブルーレイ",
    "Blu-ray",
})


def canonicalize(tags: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        if not tag:
            continue
        canonical = _HARDCODED_ALIAS_MAP.get(tag, tag)
        if canonical in _STOPWORDS:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result
