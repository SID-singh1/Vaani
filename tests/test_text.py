import pytest

from vaani.text.cleanup import clean_transcript, collapse_repetitions
from vaani.text.scripts import chunk_text, needs_transliteration, split_sentences
from vaani.text.transliterate import transliterate


class TestCleanup:
    def test_strips_subtitle_hallucinations(self):
        assert clean_transcript("Main kal aunga. Thank you for watching!") == "Main kal aunga."
        assert clean_transcript("Report bhej do. Please like and subscribe.") == "Report bhej do."

    def test_collapses_phrase_loops(self):
        assert collapse_repetitions("apne apne avaram aur apne apne avaram aur tip ko") == "apne apne avaram aur tip ko"
        assert collapse_repetitions("audio note audio note audio note") == "audio note"

    def test_collapses_single_word_loops_of_three_or_more(self):
        assert collapse_repetitions("theek theek theek theek hai") == "theek hai"
        assert collapse_repetitions("haan haan haan haan theek hai") == "haan theek hai"

    def test_keeps_hindi_reduplication(self):
        text = "kabhi kabhi hum dheere dheere chalte hain"
        assert clean_transcript(text) == text

    def test_empty(self):
        assert clean_transcript("") == ""


class TestScripts:
    @pytest.mark.parametrize(
        "text,expected",
        [("kal meeting hai", False), ("कल मीटिंग है", True), ("کل میٹنگ ہے", True), ("meeting कल", True)],
    )
    def test_needs_transliteration(self, text, expected):
        assert needs_transliteration(text) is expected

    def test_split_sentences_handles_danda(self):
        assert split_sentences("पहला वाक्य। दूसरा वाक्य? Third one.") == ["पहला वाक्य।", "दूसरा वाक्य?", "Third one."]

    def test_chunks_respect_limit_and_preserve_text(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(40))
        chunks = chunk_text(text, 120)
        assert all(len(c) <= 120 for c in chunks)
        assert " ".join(chunks) == text

    def test_overlong_sentence_is_split_on_words(self):
        text = "word " * 100
        chunks = chunk_text(text.strip(), 50)
        assert all(len(c) <= 50 for c in chunks)
        assert " ".join(chunks) == text.strip()


class TestTransliteration:
    @pytest.mark.parametrize(
        "devanagari,expected",
        [
            # schwa deletion: word-final and medial (V C _ C V)
            ("कल", "kal"),
            ("करना", "karna"),
            ("समझना", "samajhna"),
            ("बदलना", "badalna"),
            ("मिलकर", "milkar"),
            ("अपना", "apna"),
            ("समय", "samay"),
            ("गलत", "galat"),
            # conjuncts, nukta, nasalisation
            ("बच्चा", "baccha"),
            ("मित्र", "mitra"),
            ("ज्ञान", "gyaan"),
            ("लड़का", "ladka"),
            ("ज़रूर", "zaroor"),
            ("हमें", "hamein"),
            ("करें", "karein"),
            ("हूँ", "hoon"),
            ("दोनों", "donon"),
            ("संपर्क", "sampark"),
            ("करूँगा", "karunga"),
            # vowel sequences
            ("इसलिए", "isliye"),
            ("नए", "naye"),
            ("भाई", "bhai"),
            ("पढ़ाई", "padhai"),
            ("आ", "aa"),
            # lexicon: conventional spellings and English loanwords
            ("तो", "toh"),
            ("नहीं", "nahi"),
            ("में", "mein"),
            ("क्लाइंट", "client"),
            ("मीटिंग", "meeting"),
        ],
    )
    def test_words(self, devanagari, expected):
        assert transliterate(devanagari).lower() == expected

    def test_sentence(self):
        out = transliterate("राहुल, कल सुबह क्लाइंट डेमो है। मुझे २ बजे तक अपडेट भेज देना।")
        assert out == "Raahul, kal subah client demo hai. Mujhe 2 baje tak update bhej dena."

    def test_latin_text_passes_through(self):
        assert (
            transliterate("Meeting kal hai, please slides ready rakhna.")
            == "Meeting kal hai, please slides ready rakhna."
        )

    def test_mixed_script(self):
        assert transliterate("Please कल तक report भेज देना") == "Please kal tak report bhej dena"

    def test_output_has_no_devanagari(self):
        sample = "प्रोडक्शन डेटाबेस पे टाइमआउट एरर्स आ रहे हैं, अमित तू लॉग्स चेक कर।"
        assert not needs_transliteration(transliterate(sample))
