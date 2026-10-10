"""Rule-based Devanagari to casual Romanized Hinglish.

Produces the spelling people use when texting ("karna", "nahi", "mein") rather than a
scholarly transliteration ("karanā", "nahīṃ"). The core difficulty is the inherent vowel:
every Devanagari consonant carries an implicit 'a' that Hindi speakers drop in most
positions. We apply the standard schwa-deletion rules:

  * word-final schwa is dropped (कल -> kal), except after r/y/v conjuncts (मित्र -> mitra);
  * medial schwa is dropped in a V C _ C V context, scanning right to left and never
    deleting two adjacent schwas (करना -> karna, समझना -> samajhna).

Then a few casual-spelling conventions (word-final aa -> a, ee -> i), and a small lexicon
of very frequent words whose conventional spelling the rules can't predict (तो -> toh,
वो -> woh, क्लाइंट -> client).

Used for the private engine (no LLM round-trip needed) and as the cloud engine's fallback,
so users never get Devanagari back when an LLM call fails.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_VOWELS = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऑ": "o",
    "ऍ": "e",
    "ऎ": "e",
    "ऒ": "o",
    "ॲ": "a",
}
_MATRAS = {
    "ा": "aa",
    "ि": "i",
    "ी": "ee",
    "ु": "u",
    "ू": "oo",
    "ृ": "ri",
    "ॄ": "ri",
    "ॅ": "e",
    "ॆ": "e",
    "े": "e",
    "ै": "ai",
    "ॉ": "o",
    "ॊ": "o",
    "ो": "o",
    "ौ": "au",
}
_CONSONANTS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "n",
    "च": "ch",
    "छ": "ch",
    "ज": "j",
    "झ": "jh",
    "ञ": "n",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "ऩ": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ऱ": "r",
    "ल": "l",
    "ळ": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    # Precomposed nukta letters (NFC decomposes these, but accept both forms).
    "क़": "q",
    "ख़": "kh",
    "ग़": "gh",
    "ज़": "z",
    "ड़": "d",
    "ढ़": "dh",
    "फ़": "f",
    "य़": "y",
}
_NUKTA_FORMS = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "ड": "d", "ढ": "dh", "फ": "f", "य": "y"}
_NUKTA = "़"
_HALANT = "्"
_NASALS = {"ं", "ँ"}  # anusvara, chandrabindu
_VISARGA = "ः"
_LABIALS = ("p", "ph", "b", "bh", "m")
_KEEP_FINAL_SCHWA_AFTER_CONJUNCT = {"r", "y"}

_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_WORD_RUN = re.compile(r"[ऀ-ॣ॰-ॿ]+")
_SENTENCE_START = re.compile(r"(^|[.!?]\s+)([a-z])")

_LEXICON_RAW = {
    # Function words
    "है": "hai",
    "हैं": "hain",
    "था": "tha",
    "थी": "thi",
    "थे": "the",
    "हो": "ho",
    "होगा": "hoga",
    "होगी": "hogi",
    "होंगे": "honge",
    "नहीं": "nahi",
    "नही": "nahi",
    "में": "mein",
    "मैं": "main",
    "मुझे": "mujhe",
    "तो": "toh",
    "ये": "ye",
    "यह": "yeh",
    "वो": "woh",
    "वह": "woh",
    "वे": "ve",
    "और": "aur",
    "या": "ya",
    "कि": "ki",
    "की": "ki",
    "का": "ka",
    "के": "ke",
    "को": "ko",
    "से": "se",
    "पर": "par",
    "भी": "bhi",
    "ही": "hi",
    "तक": "tak",
    "लिए": "liye",
    "लिये": "liye",
    "ना": "na",
    "न": "na",
    "जी": "ji",
    "हाँ": "haan",
    "हां": "haan",
    "अच्छा": "accha",
    "अच्छी": "acchi",
    "अच्छे": "acche",
    "कुछ": "kuch",
    "क्या": "kya",
    "क्यों": "kyun",
    "क्यूँ": "kyun",
    "कैसे": "kaise",
    "कब": "kab",
    "कहाँ": "kahan",
    "कहां": "kahan",
    "यहाँ": "yahan",
    "यहां": "yahan",
    "वहाँ": "wahan",
    "वहां": "wahan",
    "आप": "aap",
    "हम": "hum",
    "तुम": "tum",
    "तू": "tu",
    "अभी": "abhi",
    "कभी": "kabhi",
    "सभी": "sabhi",
    "वाला": "wala",
    "वाली": "wali",
    "वाले": "wale",
    "वैसे": "waise",
    "दिया": "diya",
    "किया": "kiya",
    "गया": "gaya",
    "गयी": "gayi",
    "गई": "gayi",
    "रहा": "raha",
    "रही": "rahi",
    "रहे": "rahe",
    "चाहिए": "chahiye",
    "ठीक": "theek",
    "बहुत": "bahut",
    "बस": "bas",
    "फिर": "phir",
    "लेकिन": "lekin",
    "क्योंकि": "kyunki",
    "अगर": "agar",
    "मतलब": "matlab",
    "यार": "yaar",
    "एक": "ek",
    "दो": "do",
    "तीन": "teen",
    "चार": "chaar",
    "पांच": "paanch",
    "पाँच": "paanch",
    "कल": "kal",
    "आज": "aaj",
    "परसों": "parson",
    "सुबह": "subah",
    "शाम": "shaam",
    "रात": "raat",
    "बजे": "baje",
    "घंटे": "ghante",
    "वक़्त": "waqt",
    "वक्त": "waqt",
    # English loanwords common in work notes (Whisper often writes these in Devanagari)
    "मिनट": "minute",
    "मिनट्स": "minutes",
    "ऑफिस": "office",
    "मीटिंग": "meeting",
    "टीम": "team",
    "क्लाइंट": "client",
    "प्रोजेक्ट": "project",
    "अपडेट": "update",
    "डेडलाइन": "deadline",
    "ईमेल": "email",
    "मेल": "mail",
    "कॉल": "call",
    "बग": "bug",
    "फिक्स": "fix",
    "रिपोर्ट": "report",
    "डेमो": "demo",
    "प्लीज": "please",
    "प्लीज़": "please",
    "ओके": "okay",
    "थैंक्यू": "thank you",
    "सॉरी": "sorry",
    "बेसिकली": "basically",
    "एक्चुअली": "actually",
    "टास्क": "task",
    "टेस्टिंग": "testing",
    "टेस्ट": "test",
    "प्रोडक्शन": "production",
    "सर्वर": "server",
    "डेटाबेस": "database",
    "बैकएंड": "backend",
    "फ्रंटएंड": "frontend",
    "यूआई": "UI",
    "लॉग्स": "logs",
    "पेमेंट": "payment",
    "गेटवे": "gateway",
    "शेयर": "share",
    "फाइनल": "final",
    "प्रायोरिटी": "priority",
    "स्लाइड्स": "slides",
    "प्रेजेंटेशन": "presentation",
    "लॉन्च": "launch",
    "मार्केटिंग": "marketing",
    "प्रोडक्ट": "product",
    "फीडबैक": "feedback",
    "सिंक": "sync",
    "स्टेटस": "status",
    "रिव्यू": "review",
    "कोड": "code",
    "ऐप": "app",
    "वेबसाइट": "website",
    "लिंक": "link",
    "फाइल": "file",
    "डॉक्यूमेंट": "document",
    "ब्लॉकर्स": "blockers",
    "चेक": "check",
    "अलर्ट": "alert",
    "टाइमआउट": "timeout",
    "एरर": "error",
    "एरर्स": "errors",
    "इश्यू": "issue",
    "इश्यूज": "issues",
    "रिसॉल्व": "resolve",
    "डिप्लॉय": "deploy",
    "रिलीज़": "release",
    "एक्शन": "action",
    "आइटम्स": "items",
    "समरी": "summary",
    "ट्रांसक्रिप्ट": "transcript",
}
_LEXICON = {unicodedata.normalize("NFC", k): v for k, v in _LEXICON_RAW.items()}


@dataclass
class _Akshara:
    raw: str | None  # base consonant character, None for an independent vowel
    cons: str | None
    vowel: str  # "" means explicitly vowel-less (halant or deleted schwa)
    inherent: bool = False  # vowel is the implicit schwa (candidate for deletion)
    nasal: bool = False
    visarga: bool = False


def _parse(word: str) -> list[_Akshara]:
    units: list[_Akshara] = []
    for ch in word:
        last = units[-1] if units else None
        if ch in _CONSONANTS:
            units.append(_Akshara(raw=ch, cons=_CONSONANTS[ch], vowel="a", inherent=True))
        elif ch == _NUKTA and last and last.raw in _NUKTA_FORMS:
            last.cons = _NUKTA_FORMS[last.raw]
        elif ch in _MATRAS and last and last.cons is not None and last.inherent:
            last.vowel, last.inherent = _MATRAS[ch], False
        elif ch == _HALANT and last and last.cons is not None:
            last.vowel, last.inherent = "", False
        elif ch in _VOWELS:
            units.append(_Akshara(raw=None, cons=None, vowel=_VOWELS[ch]))
        elif ch in _NASALS and last:
            last.nasal = True
        elif ch == _VISARGA and last:
            last.visarga = True
        elif ch == "ॐ":
            units.append(_Akshara(raw=None, cons=None, vowel="om"))
    # ज्ञ is pronounced "gy" in Hindi.
    for i in range(len(units) - 1):
        if units[i].raw == "ज" and units[i].vowel == "" and units[i + 1].raw == "ञ":
            units[i].cons, units[i + 1].cons = "g", "y"
    return units


def _delete_schwas(units: list[_Akshara]) -> None:
    n = len(units)
    if n > 1:
        last, prev = units[-1], units[-2]
        after_conjunct = prev.cons is not None and prev.vowel == ""
        keep = after_conjunct and last.cons in _KEEP_FINAL_SCHWA_AFTER_CONJUNCT
        keep = keep or (last.cons == "y" and prev.vowel in ("i", "ee"))  # प्रिय -> priya
        if last.inherent and not last.nasal and not keep:
            last.vowel, last.inherent = "", False
    for i in range(n - 2, -1, -1):
        unit = units[i]
        if not unit.inherent or unit.nasal:
            continue
        nxt = units[i + 1]
        if nxt.cons is None:
            # Schwa merges into a following back vowel (टाइमआउट -> taaimaaut) but survives
            # before front vowels, where a y-glide appears instead (गए -> gaye).
            if nxt.vowel in ("aa", "u", "oo", "o", "au"):
                unit.vowel, unit.inherent = "", False
            continue
        if i == 0:
            continue
        prev = units[i - 1]
        if prev.vowel and nxt.vowel:
            unit.vowel, unit.inherent = "", False


def _render(units: list[_Akshara]) -> str:
    out: list[str] = []
    n = len(units)
    for i, unit in enumerate(units):
        nxt = units[i + 1] if i + 1 < n else None
        if unit.cons:
            # Geminate ch (च्छ, च्च): "accha", "baccha".
            if unit.cons == "ch" and unit.vowel == "" and nxt is not None and nxt.cons == "ch":
                out.append("c")
            else:
                out.append(unit.cons)
        vowel = unit.vowel
        final = nxt is None
        written = "".join(out)
        if unit.cons is None and written and written[-1] in "ai" and vowel == "e":
            out.append("y")  # glide before ए: लिए -> liye, गए -> gaye
        if vowel == "aa" and nxt is not None and nxt.cons is None and nxt.vowel in ("i", "ee"):
            vowel = "a"  # भाई -> bhai, पढ़ाई -> padhai
        if final and n == 1 and unit.cons is None:
            pass  # a lone vowel word keeps its length: आ -> aa
        elif final:
            if unit.nasal:
                vowel = {"ee": "i", "e": "ei"}.get(vowel, vowel)  # nahin, mein, hamein
            else:
                vowel = {"aa": "a", "ee": "i", "oo": "u"}.get(vowel, vowel)
        if unit.nasal and not final and vowel == "oo":
            vowel = "u"  # करूँगा -> karunga
        out.append(vowel)
        if unit.nasal:
            labial_next = nxt is not None and nxt.cons in _LABIALS
            out.append("m" if labial_next else "n")
        if unit.visarga:
            out.append("h")
    return "".join(out)


def _word(word: str) -> str:
    key = unicodedata.normalize("NFC", word.replace("‌", "").replace("‍", ""))
    if key in _LEXICON:
        return _LEXICON[key]
    units = _parse(key)
    if not units:
        return ""
    _delete_schwas(units)
    return _render(units)


def transliterate(text: str) -> str:
    """Convert Devanagari words to casual Hinglish; leave Latin text and punctuation as is."""
    if not text:
        return ""
    text = text.translate(_DIGITS).replace("॥", ".").replace("।", ".")
    text = _WORD_RUN.sub(lambda m: _word(m.group(0)), text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), text)
