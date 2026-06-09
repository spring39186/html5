"""
多語言金額單位換算（程式化，取代讓 LLM 自己換算單位）
======================================================
財報常見單位跨語言混用，讓模型換算（億→百萬）常錯 10 倍。改成：
抽數時把「數字 + 原文單位」原樣抓出，這裡用程式做確定性換算。

核心：
- unit_multiplier(unit)  → 該單位相對「基本單位(1)」的倍率
- to_scale(value, unit, target_unit="百萬") → 換算成目標單位的數值
- parse_amount(text)     → 從「27,665億円 / 2,766,557 million yen / 3兆4,005億円」直接解析出基本單位數值

支援：中文(萬/億/兆)、日文(万/億/兆/百万)、英文(thousand/million/billion/trillion)、
法文(mille/million/milliard)。貨幣符號(円/日圓/元/yen/$/€/£…)會被忽略，只看數量級。
"""

from __future__ import annotations
import re

# 數量級詞 → 相對基本單位(1 元/圓/yen)的倍率。長詞優先比對（百万 要先於 万）。
_SCALE = [
    ("trillion", 1e12), ("兆", 1e12),
    ("billion", 1e9), ("milliard", 1e9), ("milliards", 1e9),
    ("百萬", 1e6), ("百万", 1e6), ("million", 1e6), ("millions", 1e6), ("mn", 1e6),
    ("億", 1e8), ("亿", 1e8),
    ("萬", 1e4), ("万", 1e4),
    ("thousand", 1e3), ("mille", 1e3), ("milliers", 1e3), ("千", 1e3),
]

# 東亞複合數（3兆4,005億 / 1兆260億）用的單位（由大到小）
_EA_COMPOUND = [("兆", 1e12), ("億", 1e8), ("亿", 1e8), ("萬", 1e4), ("万", 1e4)]

# 要忽略的貨幣/雜訊 token
_CURRENCY = ("日圓", "円", "圓", "元", "yen", "jpy", "usd", "eur", "rmb", "cny",
             "$", "＄", "€", "£", "¥", "￥")

_TARGET_MULT = {  # 目標單位 → 倍率
    "基本": 1.0, "raw": 1.0,
    "千": 1e3, "萬": 1e4, "万": 1e4,
    "百萬": 1e6, "百万": 1e6, "million": 1e6,
    "億": 1e8, "兆": 1e12,
}


def _to_float(s: str):
    """把含千分位/全形數字的字串轉 float；失敗回 None。"""
    s = (s or "").translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,"))
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _strip_currency(u: str) -> str:
    u = (u or "").lower().strip()
    for c in _CURRENCY:
        u = u.replace(c.lower(), "")
    return u.strip()


def unit_multiplier(unit: str) -> float:
    """單位字串 → 相對基本單位的倍率（找不到視為 1）。"""
    u = _strip_currency(unit)
    if not u:
        return 1.0
    for word, mult in _SCALE:
        if word in u:
            return mult
    return 1.0


def to_scale(value, unit: str = "", target_unit: str = "百萬"):
    """把 (數值, 原單位) 換算成 target_unit 表示的數值。
    例：to_scale(27665, '億円', '百萬') → 2,766,500（百萬日圓）。"""
    v = value if isinstance(value, (int, float)) else _to_float(str(value))
    if v is None:
        return None
    base = v * unit_multiplier(unit)              # → 基本單位
    return base / _TARGET_MULT.get(target_unit, 1.0)


def parse_amount(text):
    """從一段文字解析出『基本單位』數值。支援東亞複合數與西式 number+word。"""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()

    # 1) 東亞複合：3兆4,005億 / 1兆260億 / 27,665億
    total, matched, rest = 0.0, False, s
    for unit, mult in _EA_COMPOUND:
        m = re.search(r"([\d,，.０-９]+)\s*" + unit, rest)
        if m:
            num = _to_float(m.group(1))
            if num is not None:
                total += num * mult
                matched = True
                rest = rest[m.end():]
    if matched:
        return total

    # 2) 西式：number + 量級詞（million/billion/兆/百万…）
    m = re.search(r"([\d,，.０-９]+)\s*(trillion|billion|milliards?|millions?|million|"
                  r"百萬|百万|兆|億|亿|萬|万|thousand|mille|mn|bn|千)", s, re.I)
    if m:
        num = _to_float(m.group(1))
        if num is not None:
            return num * unit_multiplier(m.group(2))

    # 3) 純數字
    m2 = re.search(r"[\d,，.０-９]+", s)
    if m2:
        return _to_float(m2.group(0))
    return None


def to_million(text_or_value, unit: str = ""):
    """便利函式：解析/換算成『百萬』。給 (值,單位) 或一段含單位的文字皆可。"""
    if unit:
        return to_scale(text_or_value, unit, "百萬")
    base = parse_amount(text_or_value)
    return None if base is None else base / 1e6


def normalize_million(v, unit: str = ""):
    """把 (值, 單位) 穩健換算成『百萬』。
    - 值本身含量級詞（3兆4,005億 / 2.77 trillion）→ 直接解析（忽略單位欄）。
    - 否則 → 數字 × 單位倍率。"""
    if isinstance(v, (int, float)):
        return float(v) * unit_multiplier(unit) / 1e6
    s = str(v)
    if re.search(r"[兆億亿萬万千]|million|billion|trillion|milliard", s, re.I):
        base = parse_amount(s)
        return None if base is None else base / 1e6
    num = _to_float(s)
    return None if num is None else num * unit_multiplier(unit) / 1e6


if __name__ == "__main__":
    cases = [
        ("27,665億円", "百萬", 2_766_500),
        ("3兆4,005億円", "百萬", 3_400_500),
        ("1兆260億円", "百萬", 1_026_000),
        ("2,766,557 million yen", "百萬", 2_766_557),
        ("3,810億", "百萬", 381_000),
        ("1.2 billion", "百萬", 1_200),
    ]
    print("=== parse_amount → 百萬 ===")
    ok = True
    for text, _, expect in cases:
        got = to_million(text)
        flag = "✅" if got is not None and abs(got - expect) < 1 else "❌"
        if flag == "❌":
            ok = False
        print(f"{flag} {text:<24} → {got:,.0f} 百萬 (預期 {expect:,})")
    print("=== to_scale(值,單位) ===")
    for v, u, expect in [(27665, "億円", 2_766_500), (3_103_836, "百万円", 3_103_836),
                         (435000, "百万円", 435_000), (3.4005, "兆円", 3_400_500)]:
        got = to_scale(v, u, "百萬")
        flag = "✅" if abs(got - expect) < 1 else "❌"
        if flag == "❌":
            ok = False
        print(f"{flag} {v} {u} → {got:,.0f} 百萬 (預期 {expect:,})")
    print("ALL OK" if ok else "SOME FAILED")
