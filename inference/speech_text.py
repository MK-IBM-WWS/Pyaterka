import re

from num2words import num2words

_NUMBERS = re.compile(r"\d+")
_GROUP = re.compile(r"(?i)\bИУ\s*[-‑]?\s*5\s*[-‑–]\s*(\d{2,4})([А-ЯЁ])?\b")
_LETTERS = {"а": "а", "б": "бэ", "в": "вэ", "г": "гэ", "д": "дэ"}


def _number(digits):
    if len(digits) > 6 or (len(digits) > 1 and digits[0] == "0"):
        return " ".join(num2words(int(digit), lang="ru") for digit in digits)
    return num2words(int(digits), lang="ru")


def _group(match):
    letter = (match[2] or "").lower()
    return "и у пять, " + _number(match[1]) + (f' {_LETTERS.get(letter, letter)}' if letter else "")


def _clock(match):
    hours = _number(str(int(match[1])))
    minutes = int(match[2])
    if minutes == 0:
        return f"{hours} ровно"
    if minutes < 10:
        return f"{hours} ноль {_number(str(minutes))}"
    return f"{hours} {_number(str(minutes))}"


def normalize_speech(text):
    text = re.sub(r"\b([Пп]ят)ер(к(?:а|и|е|у|ой|ою))\b", r"\1ёр\2", text)
    text = _GROUP.sub(_group, text)
    text = re.sub(r"(?i)\bИУ\s*[-‑]?\s*5\b", "и у пять", text)
    text = re.sub(r"(?i)\bН\.\s*Э\.\s*Баумана\b", "Баумана", text)
    text = re.sub(r"(?i)\bМГТУ\b", "эм гэ тэ у", text)
    text = re.sub(r"(?<!\d)([0-2]?\d):([0-5]\d)(?!\d)", _clock, text)
    return _NUMBERS.sub(lambda match: _number(match[0]), text)
