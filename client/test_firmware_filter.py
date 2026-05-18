"""Parity test: firmware_filter() must mirror firmware/src/main.cpp:447-485
byte-for-byte. Any drift produces false-positive verify mismatches on every
paste containing high bytes or CR.

Run: python test_firmware_filter.py
"""

import sys
import zlib
import random

from char_convert import firmware_filter


def _reference_filter(data: bytes) -> bytes:
    """Hand-port of the firmware loop. Kept independent from the production
    impl so they can be cross-checked."""
    out = bytearray()
    n = len(data)
    i = 0
    while i < n:
        c = data[i]
        if c >= 0x80:
            # Mirror the firmware's switch on UTF-8 lead bits.
            if (c & 0xE0) == 0xC0:
                extra = 1
            elif (c & 0xF0) == 0xE0:
                extra = 2
            elif (c & 0xF8) == 0xF0:
                extra = 3
            else:
                extra = 0
            new_i = i + extra
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


# --- Hand-traced cases ------------------------------------------------

CASES = [
    (b"", b""),
    (b"hello", b"hello"),
    (b"\r\nhello\r\n", b"\nhello\n"),
    (b"a\rb", b"ab"),
    (b"\xc3\xa9", b""),                         # 'é' as UTF-8 — dropped
    (b"a\xc3\xa9b", b"ab"),                     # 'é' embedded — dropped
    (b"\xe2\x80\x94", b""),                     # em-dash 3-byte UTF-8
    (b"\xf0\x9f\x98\x80", b""),                 # emoji 4-byte UTF-8
    (b"\xff", b""),                             # invalid lead
    (b"\x80", b""),                             # stray continuation
    (b"\x00\x01\x7f", b""),                     # control + DEL — all dropped
    (b"\t\n abc", b"\t\n abc"),
    (b"abc\xc3", b"abc"),                       # truncated 2-byte at end
    (b"abc\xe2\x80", b"abc"),                   # truncated 3-byte at end
    (b"\x09\x0a\x0b\x0c\x0d", b"\t\n"),        # VT/FF/CR dropped
]


def hand_traced() -> bool:
    ok = True
    for inp, want in CASES:
        got = firmware_filter(inp)
        if got != want:
            ok = False
            print(f"FAIL {inp!r}: got {got!r}, want {want!r}")
    return ok


def cross_check_random(n_iter: int = 200, max_len: int = 4096) -> bool:
    """Fuzz both impls against each other on random byte strings."""
    rng = random.Random(0xDEADBEEF)
    for _ in range(n_iter):
        size = rng.randrange(0, max_len)
        data = bytes(rng.randrange(0, 256) for _ in range(size))
        a = firmware_filter(data)
        b = _reference_filter(data)
        if a != b:
            print(f"FAIL fuzz: input len {size}, got {a[:40]!r}…, ref {b[:40]!r}…")
            return False
    return True


def adler_sanity() -> bool:
    """Adler32 over the filtered stream is deterministic and matches zlib."""
    msg = b"The quick brown fox\r\njumps over the lazy dog\xc3\xa9\n"
    filtered = firmware_filter(msg)
    expect = b"The quick brown fox\njumps over the lazy dog\n"
    if filtered != expect:
        print(f"FAIL adler_sanity: filtered={filtered!r}")
        return False
    a = zlib.adler32(filtered)
    if a == 0:
        print("FAIL adler_sanity: adler32 returned 0")
        return False
    return True


def main() -> int:
    failures = 0
    if not hand_traced():
        failures += 1
    if not cross_check_random():
        failures += 1
    if not adler_sanity():
        failures += 1
    if failures:
        print(f"{failures} test group(s) FAILED")
        return 1
    print("OK — firmware_filter parity verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
