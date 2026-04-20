#!/usr/bin/env python3
"""Phonomir: a phonetic cipher tool for English text.

Built from two bidirectional operations:
    1. English ↔ phonemes (via CMU Pronouncing Dictionary, suffix fallback,
       and g2p_en neural fallback going forward; via CMU reverse index,
       suffix-reverse, and a respell fallback going backward).
    2. Phonemes ↔ phonemes, per a user-configurable rule table loaded from
       a text file. Rule sets don't have to be self-inverse — the loader
       computes a reverse map by inverting the forward map.

CLI subcommands expose each operation and both directions, and a
translation-dictionary cache short-circuits repeat words and acts as the
tool's own dictionary over time.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

import cmudict
from g2p_en import G2p
import pronouncing

_g2p = G2p()

# ---------------------------------------------------------------------------
# Default rules file location
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_RULES_PATH = _REPO_ROOT / "rules" / "default.txt"

# ---------------------------------------------------------------------------
# Respell table (for "pretty" output — not round-trippable)
# ---------------------------------------------------------------------------
RESPELL = {
    "B": "b",   "P": "p",   "D": "d",   "T": "t",
    "G": "g",   "K": "k",   "F": "f",   "V": "v",
    "TH": "th", "DH": "th", "S": "s",   "Z": "z",
    "SH": "sh", "ZH": "zh", "CH": "ch", "JH": "j",
    "M": "m",   "N": "n",   "L": "l",   "R": "r",
    "W": "w",   "Y": "y",   "HH": "h",  "NG": "ng",
    "IY": "ee", "UW": "oo",
    "IH": "i",  "UH": "uh",
    "EY": "ay", "OW": "oh",
    "EH": "eh", "AO": "aw",
    "AE": "a",  "AA": "ah",
    "AY": "eye", "AW": "ow",
    "OY": "oy",
    "ER": "er",
}

# IPA table for verbose display
IPA = {
    "B": "b",   "P": "p",   "D": "d",   "T": "t",
    "G": "g",   "K": "k",   "F": "f",   "V": "v",
    "TH": "\u03b8", "DH": "\u00f0", "S": "s", "Z": "z",
    "SH": "\u0283", "ZH": "\u0292", "CH": "t\u0283", "JH": "d\u0292",
    "M": "m",   "N": "n",   "L": "l",   "R": "\u0279",
    "W": "w",   "Y": "j",   "HH": "h",  "NG": "\u014b",
    "IY": "i\u02d0", "UW": "u\u02d0",
    "IH": "\u026a", "UH": "\u028a",
    "EY": "e\u026a", "OW": "o\u028a",
    "EH": "\u025b", "AO": "\u0254",
    "AE": "\u00e6", "AA": "\u0251",
    "AY": "a\u026a", "AW": "a\u028a",
    "OY": "\u0254\u026a",
    "ER": "\u025d",
}

# ---------------------------------------------------------------------------
# Rules file loader
# ---------------------------------------------------------------------------
_RULE_LINE_RE = re.compile(r"^\s*(\S+)\s*(<->|->)\s*(\S+)\s*$")


def load_rules(path=None):
    """Parse a rules file and return (forward_map, reverse_map).

    Supports `A <-> B` (reciprocal) and `A -> B` (one-directional) lines.
    Blank lines and `#` comments ignored. Inline `#` trailing-comments stripped.
    Raises ValueError if either map has duplicate keys (ambiguous translation).
    Raises FileNotFoundError with clear message if the file doesn't exist.
    """
    if path is None:
        path = DEFAULT_RULES_PATH
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Rules file not found: {path}. "
            f"If you're using the default, make sure rules/default.txt exists "
            f"in the phonomir repo. Otherwise check the --rules argument."
        )

    forward = {}

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue

        m = _RULE_LINE_RE.match(line)
        if not m:
            raise ValueError(f"{path}:{lineno}: unparseable rule: {raw!r}")

        lhs, op, rhs = m.group(1), m.group(2), m.group(3)

        if lhs in forward and forward[lhs] != rhs:
            raise ValueError(
                f"{path}:{lineno}: duplicate forward key '{lhs}' "
                f"(already maps to '{forward[lhs]}', now mapped to '{rhs}')"
            )
        forward[lhs] = rhs

        if op == "<->":
            if rhs in forward and forward[rhs] != lhs:
                raise ValueError(
                    f"{path}:{lineno}: reciprocal rule '{lhs} <-> {rhs}' conflicts with "
                    f"existing forward mapping '{rhs} -> {forward[rhs]}'"
                )
            forward[rhs] = lhs

    # Invert to build reverse map
    reverse = {}
    for k, v in forward.items():
        if v in reverse and reverse[v] != k:
            raise ValueError(
                f"{path}: reverse map would be ambiguous "
                f"(both '{reverse[v]}' and '{k}' map to '{v}')"
            )
        reverse[v] = k

    return forward, reverse


def apply_rules(arpabet, rule_map):
    """Apply rule_map to a single ARPAbet phoneme.

    Lookup strategy:
      1. Try the full phoneme string (with any stress digit). Lets users write
         stress-specific rules like `AH0 <-> AH1`.
      2. If not found, strip the stress digit and try the base. Lets users
         write `P <-> B` to mean "swap P-with-any-stress to B-with-same-stress."
      3. If still not found, pass through unchanged.
    """
    # Tier 1: exact match (stress-specific rules)
    if arpabet in rule_map:
        return rule_map[arpabet]

    # Tier 2: base match (preserve stress digit)
    base = arpabet.rstrip("012")
    stress = arpabet[len(base):]
    if base in rule_map:
        return rule_map[base] + stress

    # Tier 3: passthrough
    return arpabet


def respell_phoneme(arpabet):
    """Convert an ARPAbet phoneme to readable English spelling."""
    base = arpabet.rstrip("012")
    # Historical special case for AH0 vs AH1/AH2 in pretty output
    if base == "AH":
        stress = arpabet[len(base):]
        return "u" if stress in ("1", "2") else "uh"
    return RESPELL.get(base, base.lower())


def to_ipa(arpabet):
    """Convert an ARPAbet phoneme to IPA for display."""
    base = arpabet.rstrip("012")
    if base == "AH":
        stress = arpabet[len(base):]
        return "\u0259" if stress == "0" else "\u028c"
    return IPA.get(base, base.lower())


# ---------------------------------------------------------------------------
# Text tokenization and capitalization
# ---------------------------------------------------------------------------
def tokenize(text):
    """Split text into (token, is_word) tuples. Non-word tokens (punctuation,
    whitespace) are preserved for round-tripping."""
    parts = re.findall(r"[A-Za-z']+|[^A-Za-z']+", text)
    return [(p, bool(re.match(r"[A-Za-z]", p))) for p in parts]


def match_caps(original, transformed):
    """Apply the capitalization pattern of original to transformed."""
    if not transformed:
        return transformed
    t = transformed.lower()
    if original.isupper() and len(original) > 1:
        return t.upper()
    if original[0].isupper():
        return t[0].upper() + t[1:]
    return t


# ---------------------------------------------------------------------------
# English → phonemes (op 1 forward)
# ---------------------------------------------------------------------------
def _cmu_lookup(word):
    """Direct CMU dictionary lookup. Returns list of phoneme strings or None."""
    phones_list = pronouncing.phones_for_word(word.lower())
    if phones_list:
        return phones_list[0].split()
    return None


# Suffixes ordered longest-first so we try more specific matches before generic ones.
SUFFIXES = [
    ("ation", ["EY1", "SH", "AH0", "N"], None),
    ("ition", ["IH1", "SH", "AH0", "N"], None),
    ("iness", ["IY0", "N", "AH0", "S"], None),
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
    ("ed", None, None),
    ("en", ["AH0", "N"], None),
    ("es", None, None),
    ("s", None, None),
]


def _ed_suffix_phones(root_phones):
    """Pronunciation of -ed based on root's final phoneme."""
    if not root_phones:
        return ["D"]
    last = root_phones[-1].rstrip("012")
    if last in ("T", "D"):
        return ["IH0", "D"]
    voiceless = {"P", "K", "F", "S", "SH", "TH", "CH", "HH"}
    return ["T"] if last in voiceless else ["D"]


def _s_suffix_phones(root_phones):
    """Pronunciation of -s/-es based on root's final phoneme."""
    if not root_phones:
        return ["Z"]
    last = root_phones[-1].rstrip("012")
    if last in ("S", "Z", "SH", "ZH", "CH", "JH"):
        return ["IH0", "Z"]
    voiceless = {"P", "K", "F", "T", "TH", "HH"}
    return ["S"] if last in voiceless else ["Z"]


def _suffix_fallback(word):
    """Try stripping suffixes, look up root, reattach. Returns phonemes or None."""
    w = word.lower()
    for suffix, phones, _ in SUFFIXES:
        if not w.endswith(suffix) or len(w) <= len(suffix) + 1:
            continue

        root = w[:-len(suffix)]
        root_phones = _cmu_lookup(root)

        if root_phones is None and root.endswith("i"):
            root_phones = _cmu_lookup(root[:-1] + "y")
        if root_phones is None:
            root_phones = _cmu_lookup(root + "e")
        if root_phones is None:
            if len(root) >= 3 and root[-1] == root[-2]:
                root_phones = _cmu_lookup(root[:-1])

        if root_phones is None:
            continue

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
    """Look up ARPAbet pronunciation for a word via CMU → suffix fallback → g2p_en."""
    result = _cmu_lookup(word)
    if result:
        return result

    cleaned = re.sub(r"[^a-zA-Z']", "", word).lower()
    if cleaned and cleaned != word.lower():
        result = _cmu_lookup(cleaned)
        if result:
            return result

    result = _suffix_fallback(cleaned or word)
    if result:
        return result

    phones = _g2p(cleaned or word)
    result = [p.strip() for p in phones if p.strip() and p not in (" ", "'")]
    return result or None


# ---------------------------------------------------------------------------
# Phonemes → English (op 1 reverse, three-tier fallback)
# ---------------------------------------------------------------------------
_reverse_index = None


def _build_reverse_index():
    """Build the {phoneme_base_tuple: word} index from CMU. Cached globally."""
    global _reverse_index
    if _reverse_index is not None:
        return
    _reverse_index = {}
    d = cmudict.dict()
    for word, pronunciations in d.items():
        if not word.isalpha():
            continue
        for pron in pronunciations:
            bases = tuple(p.rstrip("012") for p in pron)
            if bases not in _reverse_index:
                _reverse_index[bases] = word


def cmu_reverse_lookup(phones):
    """Look up phonemes in the CMU reverse index. Returns a word or None."""
    _build_reverse_index()
    bases = tuple(p.rstrip("012") for p in phones)
    return _reverse_index.get(bases)


def _suffix_reverse(phones):
    """Given phonemes, try stripping a known suffix pattern, look up root,
    and reconstruct the word. Returns a reconstructed word or None."""
    _build_reverse_index()

    # Try all fixed-phoneme suffixes first (those with a known phone list)
    for suffix, suffix_phones, _ in SUFFIXES:
        if suffix_phones is None:
            continue
        n = len(suffix_phones)
        if len(phones) <= n:
            continue
        tail_bases = tuple(p.rstrip("012") for p in phones[-n:])
        expected_bases = tuple(p.rstrip("012") for p in suffix_phones)
        if tail_bases != expected_bases:
            continue
        root_bases = tuple(p.rstrip("012") for p in phones[:-n])
        root_word = _reverse_index.get(root_bases)
        if root_word is not None:
            return root_word + suffix

    # Heuristic -ed: tail is T, D, or IH0 D
    if len(phones) >= 2:
        last_base = phones[-1].rstrip("012")
        if last_base in ("T", "D"):
            # Single T/D tail
            root_bases = tuple(p.rstrip("012") for p in phones[:-1])
            root_word = _reverse_index.get(root_bases)
            if root_word:
                return root_word + "ed"
            # IH0 D tail (loaded, wanted)
            if last_base == "D" and len(phones) >= 3 and phones[-2].rstrip("012") == "IH":
                root_bases = tuple(p.rstrip("012") for p in phones[:-2])
                root_word = _reverse_index.get(root_bases)
                if root_word:
                    return root_word + "ed"

    # Heuristic -s/-es: tail is S, Z, or IH0 Z
    if len(phones) >= 2:
        last_base = phones[-1].rstrip("012")
        if last_base in ("S", "Z"):
            if len(phones) >= 3 and phones[-2].rstrip("012") == "IH":
                root_bases = tuple(p.rstrip("012") for p in phones[:-2])
                root_word = _reverse_index.get(root_bases)
                if root_word:
                    return root_word + "es"
            root_bases = tuple(p.rstrip("012") for p in phones[:-1])
            root_word = _reverse_index.get(root_bases)
            if root_word:
                return root_word + "s"

    return None


def spell(phones):
    """Convert phonemes to text via three-tier fallback:
       CMU reverse index → suffix-reverse → respell."""
    if not phones:
        return ""

    # Tier 1: direct CMU reverse lookup
    word = cmu_reverse_lookup(phones)
    if word:
        return word

    # Tier 2: suffix-reverse
    word = _suffix_reverse(phones)
    if word:
        return word

    # Tier 3: respell (best-guess phonetic spelling)
    return "".join(respell_phoneme(p) for p in phones)


# ---------------------------------------------------------------------------
# Translation dictionary cache
# ---------------------------------------------------------------------------
_CACHE_LINE_RE = re.compile(r"^\s*(\S+)\s+\((.+?)\)\s*->\s*(\S+)\s+\((.+?)\)\s*$")


def default_cache_path(rules_path):
    """Return the cache file path for a given rules file path."""
    p = Path(rules_path)
    if p.suffix == ".txt":
        return p.with_suffix(".dict.txt")
    return p.parent / (p.name + ".dict.txt")


def load_cache(path):
    """Parse the cache file, return (forward_index, reverse_index).

    forward_index: {original_word_lower: (orig_word, orig_phones, mirr_word, mirr_phones)}
    reverse_index: {mirrored_word_lower: (orig_word, orig_phones, mirr_word, mirr_phones)}
    """
    path = Path(path)
    forward_index = {}
    reverse_index = {}

    if not path.exists():
        return forward_index, reverse_index

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _CACHE_LINE_RE.match(line)
        if not m:
            continue
        orig_word, orig_phones, mirr_word, mirr_phones = m.groups()
        entry = (orig_word, orig_phones, mirr_word, mirr_phones)
        # Overwrite duplicates — newest entry wins
        forward_index[orig_word.lower()] = entry
        reverse_index[mirr_word.lower()] = entry

    return forward_index, reverse_index


def append_cache(path, orig_word, orig_phones, mirr_word, mirr_phones):
    """Append a new four-part entry to the cache file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{orig_word} ({orig_phones}) -> {mirr_word} ({mirr_phones})\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------------------
# Intermediate phoneme format for pipeline composition
# ---------------------------------------------------------------------------
_PHONEME_RE = re.compile(r"^[A-Z]+[012]?$")
_SEGMENT_SEPARATOR = " | "


def serialize_phonemes(items):
    """Serialize a list of (token, phones_or_None) tuples.

    Word tokens with phonemes → space-separated ARPAbet.
    Non-word tokens or phonemize failures → literal text.
    Segments joined by ` | `.
    """
    parts = []
    for token, phones in items:
        if phones:
            parts.append(" ".join(phones))
        else:
            parts.append(token)
    return _SEGMENT_SEPARATOR.join(parts)


def parse_phonemes(text):
    """Parse the intermediate format back to (segment_text, phones_or_None) pairs."""
    result = []
    for segment in text.split(_SEGMENT_SEPARATOR):
        tokens = segment.split()
        if tokens and all(_PHONEME_RE.match(t) for t in tokens):
            result.append((segment, tokens))
        else:
            result.append((segment, None))
    return result


# ---------------------------------------------------------------------------
# Core translation pipelines
# ---------------------------------------------------------------------------
def mirror_word(word, forward_map):
    """Forward-translate a single word. Returns a dict or None.

    The output text goes through spell()'s three-tier fallback
    (CMU reverse → suffix-reverse → respell). Matches the plan's
    `mirror = phonemize → swap → spell`.
    """
    phones = get_phones(word)
    if phones is None:
        return None

    swapped = [apply_rules(p, forward_map) for p in phones]
    output = spell(swapped)

    return {
        "original": word,
        "arpabet": phones,
        "swapped": swapped,
        "respelled": output,
    }


def mirror_text(text, forward_map, *, verbose=False,
                cache_indexes=None, cache_path=None):
    """Full forward pipeline: tokenize → per-word (cache|mirror_word) → assemble."""
    forward_index = cache_indexes[0] if cache_indexes else {}
    reverse_index = cache_indexes[1] if cache_indexes else {}

    tokens = tokenize(text)
    output_parts = []
    verbose_lines = []

    for token, is_word in tokens:
        if not is_word:
            output_parts.append(token)
            continue

        # Cache short-circuit (checks in-memory index, which we keep updated
        # during this run so same-word repeats don't re-run the pipeline or
        # append duplicates to the cache file).
        cached = forward_index.get(token.lower())
        if cached is not None:
            _, _, mirr_word, _ = cached
            out = match_caps(token, mirr_word)
            output_parts.append(out)
            if verbose:
                verbose_lines.append(f"  {token:<16} [cache] \u2192 {out}")
            continue

        result = mirror_word(token, forward_map)
        if result is None:
            output_parts.append(f"[?{token}]")
            if verbose:
                verbose_lines.append(f"  {token:<16} ???")
            continue

        out = match_caps(token, result["respelled"])
        output_parts.append(out)

        if verbose:
            ipa_orig = " ".join(to_ipa(p) for p in result["arpabet"])
            ipa_swap = " ".join(to_ipa(p) for p in result["swapped"])
            verbose_lines.append(
                f"  {token:<16} /{ipa_orig}/  \u2192  /{ipa_swap}/  \u2192  {out}"
            )

        if cache_path is not None:
            orig_phones_str = " ".join(result["arpabet"])
            mirr_phones_str = " ".join(result["swapped"])
            append_cache(cache_path, token, orig_phones_str,
                         result["respelled"], mirr_phones_str)
            # Update in-memory indexes so later occurrences of this word (or
            # later cache reads) see the entry we just wrote.
            entry = (token, orig_phones_str, result["respelled"], mirr_phones_str)
            forward_index[token.lower()] = entry
            reverse_index[result["respelled"].lower()] = entry

    mirrored = "".join(output_parts)

    if verbose:
        print("\n".join(verbose_lines), file=sys.stderr)
        print(file=sys.stderr)

    return mirrored


def reverse_word(word, reverse_map):
    """Reverse-translate a single word. Returns best-guess original or None."""
    phones = get_phones(word)
    if phones is None:
        return None
    unswapped = [apply_rules(p, reverse_map) for p in phones]
    return spell(unswapped)


def reverse_text(text, reverse_map, *, verbose=False, cache_indexes=None):
    """Full reverse pipeline: tokenize → per-word (cache|reverse_word) → assemble."""
    reverse_index = cache_indexes[1] if cache_indexes else {}

    tokens = tokenize(text)
    output_parts = []
    verbose_lines = []

    for token, is_word in tokens:
        if not is_word:
            output_parts.append(token)
            continue

        # Cache short-circuit
        cached = reverse_index.get(token.lower())
        if cached is not None:
            orig_word, _, _, _ = cached
            out = match_caps(token, orig_word)
            output_parts.append(out)
            if verbose:
                verbose_lines.append(f"  {token:<16} [cache] \u2192 {out}")
            continue

        recovered = reverse_word(token, reverse_map)
        if recovered is None:
            output_parts.append(f"[?{token}]")
            if verbose:
                verbose_lines.append(f"  {token:<16} ???")
            continue

        out = match_caps(token, recovered)
        output_parts.append(out)

        if verbose:
            verbose_lines.append(f"  {token:<16} \u2192 {out}")

    result = "".join(output_parts)

    if verbose:
        print("\n".join(verbose_lines), file=sys.stderr)
        print(file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# Dictionary pair scanner (exploratory feature)
# ---------------------------------------------------------------------------
def scan_dictionary_pairs(forward_map, words=None):
    """Scan the CMU dictionary for word pairs where mirroring one yields
    the other as a real English word."""
    _build_reverse_index()
    if words is None:
        words = sorted(cmudict.dict().keys())

    pairs = []
    seen = set()
    for word in words:
        if not word.isalpha():
            continue
        result = mirror_word(word, forward_map)
        if result is None:
            continue
        partner = cmu_reverse_lookup(result["swapped"])
        if partner is None or partner.lower() == word.lower():
            continue
        pair_key = tuple(sorted([word.lower(), partner.lower()]))
        if pair_key in seen:
            continue
        seen.add(pair_key)
        pairs.append((word, partner))

    return pairs


# ---------------------------------------------------------------------------
# Interactive REPL (forward mirror only)
# ---------------------------------------------------------------------------
def repl(forward_map, cache_indexes, cache_path):
    print("Phonomir \u2014 Phonetic Cipher")
    print("Type a sentence to translate it. Use -v prefix for verbose. Ctrl+C to exit.\n")
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
        print(mirror_text(line, forward_map, verbose=verbose,
                          cache_indexes=cache_indexes, cache_path=cache_path))
        print()


# ---------------------------------------------------------------------------
# Cache subcommand handlers
# ---------------------------------------------------------------------------
def cache_add_cmd(original, mirrored, rules_path):
    orig_phones = get_phones(original)
    if orig_phones is None:
        raise ValueError(f"could not phonemize original word: {original}")
    mirr_phones = get_phones(mirrored)
    if mirr_phones is None:
        raise ValueError(f"could not phonemize mirrored word: {mirrored}")
    cache_path = default_cache_path(rules_path)
    append_cache(cache_path, original, " ".join(orig_phones),
                 mirrored, " ".join(mirr_phones))
    print(f"added: {original} ({' '.join(orig_phones)}) "
          f"-> {mirrored} ({' '.join(mirr_phones)})", file=sys.stderr)


def cache_get_cmd(word, rules_path):
    cache_path = default_cache_path(rules_path)
    forward_index, reverse_index = load_cache(cache_path)
    w = word.lower()
    found = False
    if w in forward_index:
        o, op, m, mp = forward_index[w]
        print(f"{o} ({op}) -> {m} ({mp})")
        found = True
    if w in reverse_index:
        o, op, m, mp = reverse_index[w]
        entry = f"{o} ({op}) -> {m} ({mp})"
        # Don't print the same entry twice if word appears on both sides (rare)
        if not (found and w in forward_index
                and forward_index[w] == reverse_index[w]):
            print(entry)
        found = True
    if not found:
        print(f"not found in cache: {word}", file=sys.stderr)
        sys.exit(1)


def cache_list_cmd(rules_path):
    cache_path = default_cache_path(rules_path)
    if not cache_path.exists():
        print(f"cache empty (file does not exist): {cache_path}", file=sys.stderr)
        return
    text = cache_path.read_text(encoding="utf-8")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cache_remove_cmd(word, rules_path):
    cache_path = default_cache_path(rules_path)
    if not cache_path.exists():
        print(f"cache empty (file does not exist): {cache_path}", file=sys.stderr)
        return
    kept = []
    removed = 0
    w = word.lower()
    for raw in cache_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(raw)
            continue
        m = _CACHE_LINE_RE.match(stripped)
        if not m:
            kept.append(raw)
            continue
        orig, _, mirr, _ = m.groups()
        if orig.lower() == w or mirr.lower() == w:
            removed += 1
            continue
        kept.append(raw)
    cache_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    print(f"removed {removed} entries matching '{word}' from {cache_path}",
          file=sys.stderr)


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------
_AUTO_OUTPUT = object()


def derive_output_path(input_path):
    """Pick a default output filename when -o is given without a value."""
    if input_path is not None:
        p = Path(input_path)
        if p.suffix:
            return p.with_name(f"{p.stem}.mirrored{p.suffix}")
        return p.with_name(f"{p.name}.mirrored")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(f"phonomir-{stamp}.txt")


def _resolve_input(args, parser, arg_name):
    """Resolve input text from positional arg, -f file, or stdin."""
    text_val = getattr(args, arg_name, None)
    file_val = getattr(args, "file", None)

    if file_val and text_val:
        parser.error(f"cannot combine positional {arg_name} with -f/--file")

    if file_val:
        try:
            return Path(file_val).read_text(encoding="utf-8"), file_val
        except FileNotFoundError:
            parser.error(f"input file not found: {file_val}")

    if text_val:
        return text_val, None

    if not sys.stdin.isatty():
        return sys.stdin.read(), None

    parser.print_help()
    sys.exit(0)


def _emit_output(args, text, input_path):
    """Write to stdout or file based on -o flag."""
    output_val = getattr(args, "output", None)
    if output_val is None:
        print(text)
        return
    out_path = (derive_output_path(input_path)
                if output_val is _AUTO_OUTPUT
                else Path(output_val))
    out_path.write_text(text, encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------
def run_mirror(args, parser):
    if args.scan_pairs:
        forward_map, _ = load_rules(args.rules)
        pairs = scan_dictionary_pairs(forward_map)
        for word, partner in pairs:
            print(f"  {word:<16} \u2194  {partner}")
        print(f"\n{len(pairs)} word pairs found.")
        return

    rules_path = Path(args.rules) if args.rules else DEFAULT_RULES_PATH
    forward_map, _ = load_rules(args.rules)

    if args.no_cache:
        cache_indexes = ({}, {})
        cache_path = None
    else:
        cache_path = Path(args.cache) if args.cache else default_cache_path(rules_path)
        cache_indexes = load_cache(cache_path)

    if args.interactive:
        repl(forward_map, cache_indexes, cache_path)
        return

    text, input_path = _resolve_input(args, parser, "text")
    mirrored = mirror_text(text, forward_map, verbose=args.verbose,
                           cache_indexes=cache_indexes, cache_path=cache_path)
    _emit_output(args, mirrored, input_path)


def run_reverse(args, parser):
    rules_path = Path(args.rules) if args.rules else DEFAULT_RULES_PATH
    _, reverse_map = load_rules(args.rules)

    if args.no_cache:
        cache_indexes = ({}, {})
    else:
        cache_path = Path(args.cache) if args.cache else default_cache_path(rules_path)
        cache_indexes = load_cache(cache_path)

    text, input_path = _resolve_input(args, parser, "text")
    result = reverse_text(text, reverse_map, verbose=args.verbose,
                          cache_indexes=cache_indexes)
    _emit_output(args, result, input_path)


def run_phonemize(args, parser):
    text, input_path = _resolve_input(args, parser, "text")
    tokens = tokenize(text)
    items = []
    for token, is_word in tokens:
        if is_word:
            phones = get_phones(token)
            items.append((token, phones))
        else:
            items.append((token, None))
    _emit_output(args, serialize_phonemes(items), input_path)


def run_spell(args, parser):
    text, input_path = _resolve_input(args, parser, "phonemes")
    segments = parse_phonemes(text)
    parts = []
    for segment_text, phones in segments:
        if phones is None:
            parts.append(segment_text)
        else:
            parts.append(spell(phones))
    _emit_output(args, "".join(parts), input_path)


def run_swap(args, parser):
    text, input_path = _resolve_input(args, parser, "phonemes")
    forward_map, reverse_map = load_rules(args.rules)
    rule_map = reverse_map if args.reverse else forward_map
    segments = parse_phonemes(text)
    items = []
    for segment_text, phones in segments:
        if phones is None:
            items.append((segment_text, None))
        else:
            swapped = [apply_rules(p, rule_map) for p in phones]
            items.append((segment_text, swapped))
    _emit_output(args, serialize_phonemes(items), input_path)


def run_cache(args, parser):
    rules_path = Path(args.rules) if args.rules else DEFAULT_RULES_PATH
    if args.cache_cmd == "add":
        cache_add_cmd(args.original, args.mirrored, rules_path)
    elif args.cache_cmd == "get":
        cache_get_cmd(args.word, rules_path)
    elif args.cache_cmd == "list":
        cache_list_cmd(rules_path)
    elif args.cache_cmd == "remove":
        cache_remove_cmd(args.word, rules_path)
    else:
        parser.error("cache subcommand required: add, get, list, or remove")


# ---------------------------------------------------------------------------
# CLI setup
# ---------------------------------------------------------------------------
_KNOWN_SUBCOMMANDS = {"translate", "reverse", "phonemize", "spell", "swap", "cache"}


def _maybe_inject_translate(argv):
    """If the user didn't specify a subcommand, inject 'translate' so the
    bare `phonomir "text"` UX keeps working. Leave --help alone."""
    if not argv:
        return argv
    if argv[0] in ("-h", "--help"):
        return argv
    if argv[0] in _KNOWN_SUBCOMMANDS:
        return argv
    return ["translate"] + argv


def _add_io_flags(sp, include_verbose=False):
    sp.add_argument("-f", "--file", help="Read input from this UTF-8 file")
    sp.add_argument("-o", "--output", nargs="?", const=_AUTO_OUTPUT, default=None,
                    help="Write output to a file (auto-named if no value)")
    if include_verbose:
        sp.add_argument("-v", "--verbose", action="store_true",
                        help="Show intermediate phoneme stages on stderr")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="phonomir",
        description="Phonomir: phonetic cipher tool for English text."
    )
    subparsers = parser.add_subparsers(dest="command")

    # translate (also the default)
    p_mir = subparsers.add_parser("translate",
        help="Forward translation: phonemize → swap → respell")
    p_mir.add_argument("text", nargs="?")
    p_mir.add_argument("--rules", default=None,
                       help=f"Rules file (default: {DEFAULT_RULES_PATH})")
    p_mir.add_argument("--cache", default=None,
                       help="Cache file (default: alongside rules file)")
    p_mir.add_argument("--no-cache", action="store_true",
                       help="Skip cache read and write")
    p_mir.add_argument("-i", "--interactive", action="store_true",
                       help="Interactive REPL mode")
    p_mir.add_argument("-c", "--scan-pairs", action="store_true",
                       help="Scan CMU dictionary for word pairs whose translations "
                            "are also real English words")
    _add_io_flags(p_mir, include_verbose=True)

    # reverse
    p_rev = subparsers.add_parser("reverse",
        help="Reverse translation: phonemize → swap (inverted) → spell")
    p_rev.add_argument("text", nargs="?")
    p_rev.add_argument("--rules", default=None)
    p_rev.add_argument("--cache", default=None)
    p_rev.add_argument("--no-cache", action="store_true")
    _add_io_flags(p_rev, include_verbose=True)

    # phonemize
    p_phon = subparsers.add_parser("phonemize",
        help="English → phonemes (intermediate format)")
    p_phon.add_argument("text", nargs="?")
    _add_io_flags(p_phon)

    # spell
    p_spell = subparsers.add_parser("spell",
        help="Phonemes → text (three-tier fallback)")
    p_spell.add_argument("phonemes", nargs="?")
    _add_io_flags(p_spell)

    # swap
    p_swap = subparsers.add_parser("swap",
        help="Apply rule table to a phoneme stream")
    p_swap.add_argument("phonemes", nargs="?")
    p_swap.add_argument("--reverse", action="store_true",
                        help="Apply the inverted rule map")
    p_swap.add_argument("--rules", default=None)
    _add_io_flags(p_swap)

    # cache
    p_cache = subparsers.add_parser("cache",
        help="Inspect or edit the translation dictionary")
    cache_subs = p_cache.add_subparsers(dest="cache_cmd")
    p_cache_add = cache_subs.add_parser("add")
    p_cache_add.add_argument("original")
    p_cache_add.add_argument("mirrored")
    p_cache_add.add_argument("--rules", default=None)
    p_cache_get = cache_subs.add_parser("get")
    p_cache_get.add_argument("word")
    p_cache_get.add_argument("--rules", default=None)
    p_cache_list = cache_subs.add_parser("list")
    p_cache_list.add_argument("--rules", default=None)
    p_cache_remove = cache_subs.add_parser("remove")
    p_cache_remove.add_argument("word")
    p_cache_remove.add_argument("--rules", default=None)

    return parser


def main():
    argv = _maybe_inject_translate(sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    dispatch = {
        "translate": run_mirror,
        "reverse": run_reverse,
        "phonemize": run_phonemize,
        "spell": run_spell,
        "swap": run_swap,
        "cache": run_cache,
    }

    try:
        dispatch[args.command](args, parser)
    except FileNotFoundError as e:
        parser.error(str(e))
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
