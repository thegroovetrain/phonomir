#!/usr/bin/env python3
"""Phonomir: a phonetic mirror cipher for English text.

Converts words to phonemes via CMU Pronouncing Dictionary,
swaps each phoneme with its articulatory pair, and respells
the result into readable English-ish text.
"""

import argparse
import re
import sys

import cmudict
import pronouncing

# ---------------------------------------------------------------------------
# Swap table: ARPAbet → ARPAbet (reciprocal pairs)
# ---------------------------------------------------------------------------
SWAP = {
    # Plosives (voiceless ↔ voiced)
    "P": "B",   "B": "P",
    "T": "D",   "D": "T",
    "K": "G",   "G": "K",
    # Fricatives
    "F": "V",   "V": "F",
    "TH": "DH", "DH": "TH",
    "S": "Z",   "Z": "S",
    "SH": "ZH", "ZH": "SH",
    # Affricates
    "CH": "JH", "JH": "CH",
    # Nasals / Liquids
    "M": "N",   "N": "M",
    "L": "R",   "R": "L",
    "W": "Y",   "Y": "W",
    # Vowels (front ↔ back)
    "IY": "UW", "UW": "IY",   # feet ↔ boot
    "IH": "UH", "UH": "IH",   # fit ↔ foot
    "EY": "OW", "OW": "EY",   # fate ↔ goat
    "EH": "AO", "AO": "EH",   # bet ↔ caught
    "AE": "AA", "AA": "AE",   # bat ↔ father
    "AY": "AW", "AW": "AY",   # bite ↔ bout
}

# ---------------------------------------------------------------------------
# Respelling table: ARPAbet → readable English letters
# ---------------------------------------------------------------------------
RESPELL = {
    # Consonants
    "B": "b",   "P": "p",   "D": "d",   "T": "t",
    "G": "g",   "K": "k",   "F": "f",   "V": "v",
    "TH": "th", "DH": "th", "S": "s",   "Z": "z",
    "SH": "sh", "ZH": "zh", "CH": "ch",  "JH": "j",
    "M": "m",   "N": "n",   "L": "l",   "R": "r",
    "W": "w",   "Y": "y",   "HH": "h",  "NG": "ng",
    # Vowels
    "IY": "ee", "UW": "oo",
    "IH": "i",  "UH": "uh",
    "EY": "ay", "OW": "oh",
    "EH": "eh", "AO": "aw",
    "AE": "a",  "AA": "ah",
    "AY": "eye","AW": "ow",
    "OY": "oy",
}
# AH is special: stressed (AH1/AH2) = "u", unstressed (AH0) = "uh"
# ER is decomposed before we get here, so no entry needed.

# ---------------------------------------------------------------------------
# IPA table for verbose display
# ---------------------------------------------------------------------------
IPA = {
    "B": "b",   "P": "p",   "D": "d",   "T": "t",
    "G": "g",   "K": "k",   "F": "f",   "V": "v",
    "TH": "\u03b8", "DH": "\u00f0", "S": "s",   "Z": "z",
    "SH": "\u0283", "ZH": "\u0292", "CH": "t\u0283", "JH": "d\u0292",
    "M": "m",   "N": "n",   "L": "l",   "R": "\u0279",
    "W": "w",   "Y": "j",   "HH": "h",  "NG": "\u014b",
    "IY": "i\u02d0","UW": "u\u02d0",
    "IH": "\u026a", "UH": "\u028a",
    "EY": "e\u026a", "OW": "o\u028a",
    "EH": "\u025b", "AO": "\u0254",
    "AE": "\u00e6", "AA": "\u0251",
    "AY": "a\u026a", "AW": "a\u028a",
    "OY": "\u0254\u026a",
}


def decompose_er(phones):
    """Replace ER tokens with AH + R, preserving stress on AH."""
    result = []
    for p in phones:
        base = p.rstrip("012")
        stress = p[len(base):]
        if base == "ER":
            # ER0 → AH0 R, ER1 → AH1 R, etc.
            result.append("AH" + (stress or "0"))
            result.append("R")
        else:
            result.append(p)
    return result


def swap_phoneme(arpabet):
    """Swap a single ARPAbet phoneme (with stress digit) to its mirror pair."""
    base = arpabet.rstrip("012")
    stress = arpabet[len(base):]

    # Special case: AH0 (schwa /ə/) ↔ AH1/AH2 (strut /ʌ/)
    if base == "AH":
        if stress == "0":
            return "AH1"
        else:
            return "AH0"

    swapped = SWAP.get(base, base)  # passthrough if not in table
    return swapped + stress


def respell_phoneme(arpabet):
    """Convert an ARPAbet phoneme to readable English spelling."""
    base = arpabet.rstrip("012")
    stress = arpabet[len(base):]

    if base == "AH":
        return "u" if stress in ("1", "2") else "uh"

    return RESPELL.get(base, base.lower())


def to_ipa(arpabet):
    """Convert an ARPAbet phoneme to IPA for display."""
    base = arpabet.rstrip("012")
    stress = arpabet[len(base):]

    if base == "AH":
        return "\u0259" if stress == "0" else "\u028c"

    return IPA.get(base, base.lower())


def _cmu_lookup(word):
    """Direct CMU lookup. Returns list of phoneme strings or None."""
    phones_list = pronouncing.phones_for_word(word.lower())
    if phones_list:
        return phones_list[0].split()
    return None


# Suffixes ordered longest-first so we try more specific matches before generic ones.
# Each entry: (spelling_suffix, phoneme_suffix, strip_to_get_root)
# strip_to_get_root: how many chars to remove from the word to get the root to look up.
#   None means just strip the suffix spelling length.
SUFFIXES = [
    # Multi-syllable suffixes
    ("ation", ["EY1", "SH", "AH0", "N"], None),
    ("ition", ["IH1", "SH", "AH0", "N"], None),
    ("iness", ["IY0", "N", "AH0", "S"], None),  # happi-ness (root: happy → happi)
    ("ously", ["AH0", "S", "L", "IY0"], None),
    ("ment", ["M", "AH0", "N", "T"], None),
    ("ness", ["N", "AH0", "S"], None),
    ("able", ["AH0", "B", "AH0", "L"], None),
    ("ible", ["AH0", "B", "AH0", "L"], None),
    ("tion", ["SH", "AH0", "N"], None),
    ("sion", ["ZH", "AH0", "N"], None),
    ("ful", ["F", "AH0", "L"], None),
    ("less", ["L", "AH0", "S"], None),
    ("ous", ["AH0", "S"], None),
    ("ish", ["IH0", "SH"], None),
    ("ist", ["IH0", "S", "T"], None),
    ("ity", ["IH0", "T", "IY0"], None),
    ("ive", ["IH0", "V"], None),
    ("ing", ["IH0", "NG"], None),
    ("ily", ["AH0", "L", "IY0"], None),
    ("al", ["AH0", "L"], None),
    ("ly", ["L", "IY0"], None),
    ("er", ["ER0"], None),
    ("ed", None, None),  # special: pronunciation depends on root's final phoneme
    ("en", ["AH0", "N"], None),
    ("es", None, None),  # special
    ("s", None, None),   # special
]


def _ed_suffix_phones(root_phones):
    """Determine pronunciation of -ed based on root's final phoneme."""
    if not root_phones:
        return ["D"]
    last = root_phones[-1].rstrip("012")
    if last in ("T", "D"):
        return ["IH0", "D"]
    elif last in ("P", "B", "K", "G", "F", "V", "S", "Z",
                  "SH", "ZH", "TH", "DH", "CH", "JH",
                  "M", "N", "L", "R", "NG", "W", "Y", "HH"):
        # Voiceless final → T, voiced final → D
        voiceless = {"P", "K", "F", "S", "SH", "TH", "CH", "HH"}
        return ["T"] if last in voiceless else ["D"]
    else:
        return ["D"]  # after vowels


def _s_suffix_phones(root_phones):
    """Determine pronunciation of -s/-es based on root's final phoneme."""
    if not root_phones:
        return ["Z"]
    last = root_phones[-1].rstrip("012")
    if last in ("S", "Z", "SH", "ZH", "CH", "JH"):
        return ["IH0", "Z"]
    voiceless = {"P", "K", "F", "T", "TH", "HH"}
    return ["S"] if last in voiceless else ["Z"]


def _suffix_fallback(word):
    """Try stripping suffixes, looking up the root, and reattaching."""
    w = word.lower()
    for suffix, phones, strip_n in SUFFIXES:
        if not w.endswith(suffix) or len(w) <= len(suffix) + 1:
            continue

        root = w[:-len(suffix)]

        # Try the root directly
        root_phones = _cmu_lookup(root)

        # Some suffixes change the root spelling: e.g. "grassy" → strip "y" → "grass"
        # Try adding back common root-end letters
        if root_phones is None and root.endswith("i"):
            # happi→happy, greedil→greedily... try root with y
            root_phones = _cmu_lookup(root[:-1] + "y")
        if root_phones is None:
            # Try with trailing 'e': "lov" + "e" = "love" (for "lovely")
            root_phones = _cmu_lookup(root + "e")
        if root_phones is None:
            # Try de-doubling final consonant: "batt" → "bat" (for "batting")
            if len(root) >= 3 and root[-1] == root[-2]:
                root_phones = _cmu_lookup(root[:-1])

        if root_phones is None:
            continue

        # Determine suffix phones
        if suffix == "ed":
            suffix_phones = _ed_suffix_phones(root_phones)
        elif suffix in ("s", "es"):
            suffix_phones = _s_suffix_phones(root_phones)
        elif phones is not None:
            suffix_phones = phones
        else:
            continue

        return root_phones + suffix_phones

    return None


def get_phones(word):
    """Look up ARPAbet pronunciation for a word. Returns list of phoneme strings or None."""
    # Direct CMU lookup
    result = _cmu_lookup(word)
    if result:
        return result

    # Try without trailing punctuation artifacts
    cleaned = re.sub(r"[^a-zA-Z']", "", word).lower()
    if cleaned and cleaned != word.lower():
        result = _cmu_lookup(cleaned)
        if result:
            return result

    # Suffix-stripping fallback
    result = _suffix_fallback(cleaned or word)
    if result:
        return result

    # Last resort: try g2p_en if available
    try:
        from g2p_en import G2p
        _g2p = G2p()
        phones = _g2p(word)
        result = [p.strip() for p in phones if p.strip() and p.strip() not in (" ", "'")]
        if result:
            return result
    except ImportError:
        pass

    return None


_reverse_index = None

def _build_reverse_index():
    global _reverse_index
    if _reverse_index is not None:
        return
    _reverse_index = {}
    d = cmudict.dict()
    for word, pronunciations in d.items():
        # Skip words with punctuation (like 'bout)
        if not word.isalpha():
            continue
        for pron in pronunciations:
            bases = tuple(p.rstrip("012") for p in pron)
            if bases not in _reverse_index:
                _reverse_index[bases] = word


def find_collision(swapped_phones):
    """Check if swapped phonemes match any real English word in CMU dict."""
    _build_reverse_index()
    bases = tuple(p.rstrip("012") for p in swapped_phones)
    return _reverse_index.get(bases)


def mirror_word(word):
    """Transform a single word through the phonetic mirror.

    Returns a dict with all intermediate stages, or None if the word
    can't be looked up.
    """
    phones = get_phones(word)
    if phones is None:
        return None

    # Decompose ER into AH + R
    phones = decompose_er(phones)

    # Swap each phoneme
    swapped = [swap_phoneme(p) for p in phones]

    # Respell
    respelled = "".join(respell_phoneme(p) for p in swapped)

    # Check for real-word collision
    collision = find_collision(swapped)

    return {
        "original": word,
        "arpabet": phones,
        "swapped": swapped,
        "respelled": respelled,
        "collision": collision,
    }


def match_caps(original, transformed):
    """Preserve the capitalization pattern of the original word."""
    if not transformed:
        return transformed
    if original.isupper() and len(original) > 1:
        return transformed.upper()
    if original[0].isupper():
        return transformed[0].upper() + transformed[1:]
    return transformed


def tokenize(text):
    """Split text into (token, is_word) tuples."""
    parts = re.findall(r"[A-Za-z']+|[^A-Za-z']+", text)
    return [(p, bool(re.match(r"[A-Za-z]", p))) for p in parts]


def mirror_text(text, verbose=False):
    """Transform a full text through the phonetic mirror."""
    tokens = tokenize(text)
    output_parts = []
    verbose_lines = []

    for token, is_word in tokens:
        if not is_word:
            output_parts.append(token)
            continue

        result = mirror_word(token)
        if result is None:
            output_parts.append(f"[?{token}]")
            if verbose:
                verbose_lines.append(f"  {token:<16} ???")
            continue

        respelled = match_caps(token, result["respelled"])
        output_parts.append(respelled)

        if verbose:
            ipa_orig = " ".join(to_ipa(p) for p in result["arpabet"])
            ipa_swap = " ".join(to_ipa(p) for p in result["swapped"])
            verbose_lines.append(
                f"  {token:<16} /{ipa_orig}/  \u2192  /{ipa_swap}/  \u2192  {respelled}"
            )

    mirrored = "".join(output_parts)

    if verbose:
        print("\n".join(verbose_lines))
        print()
        print("Result:")

    return mirrored


def find_collisions(words=None):
    """Find words whose mirrored phonemes match another real English word.

    If words is None, scans the entire CMU dictionary.
    """
    _build_reverse_index()
    if words is None:
        words = sorted(cmudict.dict().keys())

    pairs = []
    seen = set()
    for word in words:
        if not word.isalpha():
            continue
        result = mirror_word(word)
        if result is None or result["collision"] is None:
            continue
        collision = result["collision"]
        if collision.lower() == word.lower():
            continue
        pair_key = tuple(sorted([word.lower(), collision.lower()]))
        if pair_key in seen:
            continue
        seen.add(pair_key)
        pairs.append((word, collision))

    return pairs


def repl():
    """Interactive REPL mode."""
    print("Phonomir \u2014 Phonetic Mirror")
    print("Type a sentence to mirror it. Use -v prefix for verbose. Ctrl+C to exit.\n")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        verbose = False
        if line.startswith("-v "):
            verbose = True
            line = line[3:]
        print(mirror_text(line, verbose=verbose))
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Phonomir: phonetic mirror cipher for English text"
    )
    parser.add_argument("text", nargs="?", help="Text to transform")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show intermediate phoneme stages")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Interactive REPL mode")
    parser.add_argument("-c", "--collisions", action="store_true",
                        help="Scan CMU dictionary for real-word collisions")
    args = parser.parse_args()

    if args.collisions:
        pairs = find_collisions()
        for word, collision in pairs:
            print(f"  {word:<16} \u2194  {collision}")
        print(f"\n{len(pairs)} collision pairs found.")
        return
    elif args.interactive:
        repl()
    elif args.text:
        print(mirror_text(args.text, verbose=args.verbose))
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
        print(mirror_text(text, verbose=args.verbose))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
