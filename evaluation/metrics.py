"""Transcript accuracy metrics for code-mixed Hinglish.

Romanized Hindi has no standard spelling: "toh"/"to", "nahin"/"nahi", "theek"/"thik" are
the same word, and a speaker saying "gyarah baje" may be transcribed as "11 baje". Plain
WER counts all of these as errors, which says more about spelling conventions than about
recognition. We therefore report two numbers, always side by side:

  * WER / CER: lowercase, punctuation removed. The conventional, strict metric.
  * Hinglish-normalized WER (hWER): additionally maps digits to English number words,
    joins common split compounds ("front end" -> "frontend"), canonicalizes frequent
    Hinglish spelling variants and collapses doubled vowels (aa->a, ee->i, oo->u).
    The same transform is applied to reference and hypothesis.

hWER is lenient by construction (for example "to"/"toh" become indistinguishable), so it
should be read as "how much of the content was recognized", never quoted without WER.
"""

from __future__ import annotations

import re

import jiwer

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

_COMPOUNDS = {
    "front end": "frontend",
    "back end": "backend",
    "e mail": "email",
    "log in": "login",
    "on line": "online",
    "off line": "offline",
    "feed back": "feedback",
    "screen shot": "screenshot",
    "screen shots": "screenshots",
    "time out": "timeout",
    "up date": "update",
    "dead line": "deadline",
}

# Frequent Romanized-Hindi spelling variants -> one canonical form (applied after lowercasing).
_VARIANTS = {
    "toh": "to",
    "nahin": "nahi",
    "nhi": "nahi",
    "kia": "kya",
    "kyon": "kyun",
    "kyu": "kyun",
    "kyunki": "kyunki",
    "kyonki": "kyunki",
    "achha": "acha",
    "accha": "acha",
    "achcha": "acha",
    "acchha": "acha",
    "theek": "thik",
    "tik": "thik",
    "han": "haan",
    "yeh": "ye",
    "woh": "wo",
    "vo": "wo",
    "hamein": "hume",
    "humein": "hume",
    "hamen": "hume",
    "usmein": "usme",
    "ismein": "isme",
    "dikhein": "dikhe",
    "dikhen": "dikhe",
    "jaaye": "jaye",
    "jae": "jaye",
    "hoon": "hu",
    "hun": "hu",
    "rha": "raha",
    "rhe": "rahe",
    "rhi": "rahi",
    "bohot": "bahut",
    "bhot": "bahut",
    "kuchh": "kuch",
    "pehle": "pahle",
    "mai": "main",
    "mujhko": "mujhe",
    "karoonga": "karunga",
    "kr": "kar",
    "krna": "karna",
    "hy": "hai",
}

_basic = jiwer.Compose(
    [
        jiwer.ToLowerCase(),
        jiwer.RemovePunctuation(),
        jiwer.RemoveMultipleSpaces(),
        jiwer.Strip(),
    ]
)


def number_to_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return _ONES[hundreds] + " hundred" + ("" if rest == 0 else " " + number_to_words(rest))
    if n < 10000:
        thousands, rest = divmod(n, 1000)
        return _ONES[thousands] + " thousand" + ("" if rest == 0 else " " + number_to_words(rest))
    return str(n)


def basic_normalize(text: str) -> str:
    return _basic(text or "")


def hinglish_normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"[-–/]", " ", text)
    text = re.sub(r"\d+", lambda m: f" {number_to_words(int(m.group()))} ", text)
    text = basic_normalize(text)
    for split, joined in _COMPOUNDS.items():
        text = re.sub(rf"\b{split}\b", joined, text)
    words = []
    for word in text.split():
        word = _VARIANTS.get(word, word)
        word = re.sub(r"aa+", "a", word)
        word = re.sub(r"ee+", "i", word)
        word = re.sub(r"oo+", "u", word)
        word = word.replace("chh", "ch").replace("cch", "ch")
        words.append(word)
    return " ".join(words)


def wer(reference: str, hypothesis: str, normalize=basic_normalize) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return jiwer.wer(ref, hyp)


def cer(reference: str, hypothesis: str, normalize=basic_normalize) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return jiwer.cer(ref, hyp)


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)
