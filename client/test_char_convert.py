"""convert_to_ascii() must turn keyboard look-alikes into typeable ASCII.

Run: python test_char_convert.py
"""

import sys

from char_convert import convert_to_ascii

CASES = {
    # Dashes / minus look-alikes
    "−": "-", "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "⁃": "-", "˗": "-", "➖": "-",
    "ー": "-", "﹣": "-", "－": "-", "⸺": "--", "⸻": "---",
    "⸗": "-", "〜": "~", "­": "",
    # Quotes / primes
    "‘": "'", "’": "'", "‛": "'", "“": '"', "‟": '"',
    "′": "'", "″": '"', "ʼ": "'", "´": "'", "‹": "<",
    "›": ">",
    # Operator look-alikes
    "⁄": "/", "∕": "/", "∖": "\\", "∗": "*", "∣": "|",
    "∶": ":", "∼": "~", "ˆ": "^", "˜": "~",
    # Spaces / invisibles
    " ": " ", "　": " ", " ": " ", "​": "", "⁠": "",
    # Fullwidth ASCII
    "Ａｂｃ１！": "Abc1!",
    # Line breaks
    "a b": "a\nb", "a b": "a\nb", "ab": "a\nb",
    # Must not be flattened to '-' by the name-based fallback
    "⇢": "->", "⇠": "<-", "∓": "-/+", "⇥": "?",
    # Bullets
    "•": "*", "◦": "o", "∙": "*",
}


def main() -> int:
    failures = 0
    for src, want in CASES.items():
        got = convert_to_ascii(src)
        if got != want:
            failures += 1
            cps = " ".join(f"U+{ord(c):04X}" for c in src)
            print(f"FAIL {cps}: got {got!r}, want {want!r}")
    mixed = "a − b — “c”"
    if convert_to_ascii(mixed) != 'a - b - "c"':
        failures += 1
        print(f"FAIL mixed: {convert_to_ascii(mixed)!r}")
    if failures:
        print(f"{failures} case(s) FAILED")
        return 1
    print(f"OK — {len(CASES) + 1} conversions verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
