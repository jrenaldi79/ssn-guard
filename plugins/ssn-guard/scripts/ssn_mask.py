#!/usr/bin/env python3
"""Mask SSN-shaped strings before emitting command output."""

from __future__ import annotations

import os
import re
import sys

# Separator characters allowed between digit groups.
_SEP_CHARS = " \t\\-‐‑‒–—―−._/"
_SEP = f"[{_SEP_CHARS}]"
# Separators that tie digits into one longer number (credit cards, phone

# must still match.
_JOIN = "[\\-‐‑‒–—―−.]"

# Nine digits, with 0 to 3 separator characters between each digit.
_CANDIDATE = re.compile(
    rf"(?<!\d)(?<!\d{_JOIN})"
    rf"\d(?:{_SEP}{{0,3}}\d){{8}}"
    rf"(?!\d)(?!{_JOIN}\d)"
)

_LABEL = re.compile(
    r"(?i)(?:(?<![a-z])ssn|s\.s\.n|social[\s_\-]*sec(?:urity)?|"
    r"(?<![a-z])ss[\s_\-]*(?:#|no\b|num)|(?<![a-z])i?tin(?![a-z])|"
    r"tax[\s_\-]*(?:payer)?[\s_\-]*id)"
)
_LABEL_WINDOW = 30

_ALREADY_MASKED = "***-**-"


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def _allowlist() -> set[str]:
    raw = os.environ.get("SSN_GUARD_ALLOW", "")
    return {re.sub(r"\D", "", item) for item in raw.split(",") if item.strip()}


def is_valid_ssn_or_itin(digits: str) -> bool:
    """Return True when 9 digits follow SSA rules for an SSN, or IRS rules for an ITIN."""
    area, group, serial = int(digits[:3]), int(digits[3:5]), int(digits[5:])
    if group == 0 or serial == 0:
        return False
    if area == 0 or area == 666:
        return False
    if area >= 900:
        # ITIN: starts with 9, middle digits 50-65, 70-88, 90-92 or 94-99.
        return 50 <= group <= 65 or 70 <= group <= 88 or 90 <= group <= 92 or 94 <= group <= 99
    return True


def _groups(match_text: str) -> list[int]:
    return [len(g) for g in re.split(_SEP + "+", match_text) if g]


def _is_ssn(text: str, start: int, end: int, contiguous_ok: bool) -> bool:
    found = text[start:end]
    groups = _groups(found)
    if groups == [3, 2, 4]:
        return True
    if groups and max(groups) == 1:
        # 1 2 3 4 5 6 7 8 9. Skip it when it is part of a longer run of
        # single numbers, such as page links "1 2 3 4 5 6 7 8 9 10".
        before = text[max(0, start - 4):start]
        after = text[end:end + 4]
        if re.search(r"\d[ \t,]+$", before) or re.match(r"^[ \t,]+\d", after):
            return False
        return True
    labeled = bool(_LABEL.search(text[max(0, start - _LABEL_WINDOW):start]))
    if labeled:
        return True
    if groups == [9] and contiguous_ok:
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before.isalpha() or after.isalpha():
            return False  # part of an identifier or a hash
        return is_valid_ssn_or_itin(re.sub(r"\D", "", found))
    return False


def find_ssns(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, digits) for each SSN-like value in text."""
    if not text or not any(ch.isdigit() for ch in text):
        return []
    contiguous_ok = _env_flag("SSN_GUARD_CONTIGUOUS", True)
    allow = _allowlist()
    results = []
    for m in _CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if digits in allow:
            continue
        if _is_ssn(text, m.start(), m.end(), contiguous_ok):
            results.append((m.start(), m.end(), digits))
    return results


def mask_text(text: str) -> tuple[str, int]:
    """Return (masked text, number of values masked)."""
    hits = find_ssns(text)
    if not hits:
        return text, 0
    out, last = [], 0
    for start, end, digits in hits:
        out.append(text[last:start])
        out.append(_ALREADY_MASKED + digits[-4:])
        last = end
    out.append(text[last:])
    return "".join(out), len(hits)


def contains_ssn(text: str) -> bool:
    return bool(find_ssns(text))


_BASE64_BLOB = re.compile(r"^[A-Za-z0-9+/=\r\n]{200,}$")


def mask_value(value, mask_numbers: bool = False):
    """Mask every string inside a JSON-like value. The shape stays the same.

    Returns (new value, count). Integers are masked only when
    mask_numbers is True, because that changes their type to string.
    """
    if isinstance(value, str):
        if _BASE64_BLOB.match(value):
            return value, 0  # image or PDF bytes; text masking does not apply
        return mask_text(value)
    if isinstance(value, bool) or value is None or isinstance(value, float):
        return value, 0
    if isinstance(value, int):
        if mask_numbers:
            masked, count = mask_text(str(value))
            if count:
                return masked, count
        return value, 0
    if isinstance(value, list):
        total, out = 0, []
        for item in value:
            new, count = mask_value(item, mask_numbers)
            out.append(new)
            total += count
        return out, total
    if isinstance(value, dict):
        total, out = 0, {}
        for key, item in value.items():
            new_key, key_count = mask_value(key, False) if isinstance(key, str) else (key, 0)
            while new_key in out:
                new_key = f"{new_key}_"
            new, count = mask_value(item, mask_numbers)
            out[new_key] = new
            total += key_count + count
        return out, total
    return value, 0


def notice(count: int) -> str:
    """Describe redaction without disclosing original content."""
    return (
        f"SSN Guard redacted {count} possible SSN value(s). "
        "False positives are possible; affected values are incomplete. "
        "Only the last four digits are shown."
    )


def main() -> int:
    data = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    masked, count = mask_text(data)
    sys.stdout.write(masked)
    if count:
        sys.stderr.write(notice(count) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
