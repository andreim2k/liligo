"""Shared Unicode-to-ASCII character conversion for KeyBridge clients."""

import unicodedata


def _build_unicode_map():
    m = {}
    # Box-drawing: full range U+2500-U+257F
    # Horizontal lines -> '-'
    for cp in [
        0x2500,
        0x2504,
        0x2508,
        0x254C,
        0x2550,  # light/dashed/double horizontal
        0x2501,
        0x2505,
        0x2509,
        0x254D,
    ]:  # heavy/dashed horizontal
        m[chr(cp)] = "-"
    # Vertical lines -> '|'
    for cp in [
        0x2502,
        0x2506,
        0x250A,
        0x254E,
        0x2551,  # light/dashed/double vertical
        0x2503,
        0x2507,
        0x250B,
        0x254F,
    ]:  # heavy/dashed vertical
        m[chr(cp)] = "|"
    # All remaining box-drawing (corners, junctions, diagonals) -> '+'
    for cp in range(0x2500, 0x2580):
        if chr(cp) not in m:
            m[chr(cp)] = "+"
    # Block elements U+2580-U+259F -> '#'
    for cp in range(0x2580, 0x25A0):
        m[chr(cp)] = "#"
    return m


_UNICODE_MAP = _build_unicode_map()
_UNICODE_MAP.update(
    {
        # Smart quotes
        "“": '"',
        "”": '"',
        "„": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "«": '"',
        "»": '"',
        # Dashes and hyphens
        "—": "-",
        "–": "-",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "―": "-",
        "﹘": "-",
        "﹣": "-",
        "－": "-",
        # Spaces (non-breaking, em-space, en-space, thin, etc.)
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        " ": " ",
        # Zero-width characters (drop them)
        "​": "",
        "‌": "",
        "‍": "",
        "﻿": "",
        "‎": "",
        "‏": "",
        # Common symbols
        "…": "...",
        "•": "*",
        "‣": "*",
        "●": "*",
        "○": "o",
        "■": "#",
        "□": "[]",
        "▪": "*",
        "▫": "*",
        "°": "o",
        "§": "SS",
        "¶": "P",
        "±": "+/-",
        "×": "x",
        "÷": "/",
        "≤": "<=",
        "≥": ">=",
        "≠": "!=",
        "≈": "~=",
        "∞": "inf",
        "√": "sqrt",
        # Arrows
        "→": "->",
        "←": "<-",
        "↑": "^",
        "↓": "v",
        "⇒": "=>",
        "⇐": "<=",
        "⇔": "<=>",
        "➔": "->",
        "➜": "->",
        "➡": "->",
        # Legal/trademark
        "™": "(TM)",
        "©": "(C)",
        "®": "(R)",
        # Currency
        "€": "EUR",
        "£": "GBP",
        "¥": "YEN",
        "¢": "c",
        "₹": "INR",
        "₽": "RUB",
        "₩": "KRW",
        # Fractions
        "¼": "1/4",
        "½": "1/2",
        "¾": "3/4",
        "⅓": "1/3",
        "⅔": "2/3",
        # Superscripts / subscripts
        "²": "2",
        "³": "3",
        "¹": "1",
        "⁰": "0",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
        # Misc punctuation and symbols
        "¿": "?",
        "¡": "!",
        "№": "No.",
        "‰": "0/00",
        "‱": "0/000",
        "·": ".",
        "‧": ".",
        "・": ".",
        # Checkmarks, crosses, media symbols
        "✓": "[v]",
        "✔": "[v]",
        "✕": "[x]",
        "✖": "[x]",
        "✗": "[x]",
        "✘": "[x]",
        "⏺": "(o)",
        "⏸": "||",
        "⏹": "[]",
        "⏯": "|>",
        "▶": ">",
        "◀": "<",
        "⏵": ">",
        "⏴": "<",
        "⭐": "*",
        "★": "*",
        "☆": "*",
        "❤": "<3",
        "♥": "<3",
        # Musical notes
        "♪": "#",
        "♫": "##",
        "♬": "##",
        "♭": "b",
        "♮": "h",
        "♯": "#",
        # Accented vowels (common ones not handled by unicodedata NFKD)
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ø": "o",
        "Ø": "O",
        "ß": "ss",
        "ı": "i",
        "Đ": "D",
        "đ": "d",
        "Ł": "L",
        "ł": "l",
        "Ž": "Z",
        "ž": "z",
    }
)


def convert_to_ascii(text: str) -> str:
    """Convert all non-ASCII characters to ASCII equivalents."""
    result = []
    for c in text:
        if ord(c) <= 127:
            result.append(c)
        elif c in _UNICODE_MAP:
            result.append(_UNICODE_MAP[c])
        else:
            nfkd = unicodedata.normalize("NFKD", c)
            ascii_chars = "".join(ch for ch in nfkd if ord(ch) <= 127)
            if ascii_chars:
                result.append(ascii_chars)
            else:
                result.append("?")
    return "".join(result)


def firmware_filter(data: bytes) -> bytes:
    """Mirror firmware's per-byte filter at main.cpp:447-485 exactly.

    The firmware silently drops:
    - bytes >= 0x80 plus their UTF-8 continuation bytes (1, 2, or 3 extra
      bytes for 0xC0-, 0xE0-, 0xF0- prefix; 0 for a stray continuation
      or invalid lead — in which case only that single byte is dropped).
    - carriage return (0x0D)
    - control bytes other than \\n (0x0A) and \\t (0x09)
    - DEL (0x7F)
    It keeps \\n, \\t, and printable ASCII 0x20-0x7E.

    Used to compute the same adler32 / byte-count the firmware will,
    so the verify-channel comparison is meaningful.
    """
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        c = data[i]
        if c >= 0x80:
            if (c & 0xE0) == 0xC0:
                skip = 1
            elif (c & 0xF0) == 0xE0:
                skip = 2
            elif (c & 0xF8) == 0xF0:
                skip = 3
            else:
                skip = 0
            new_i = i + skip
            if new_i >= n:
                break
            i = new_i + 1
            continue
        if c == 0x0D:
            i += 1
            continue
        if c == 0x0A or c == 0x09 or (0x20 <= c <= 0x7E):
            out.append(c)
        i += 1
    return bytes(out)
