# Phonomir

A phonetic substitution cipher for English text. Words are transformed by mapping each phoneme through a set of cycles based on articulatory features — stops trade with fricatives, nasals with affricates, vowels rotate through a 15-step cycle. The cipher operates on sounds, not spelling.

Applying the forward transform enciphers a word. To decipher, use `reverse` — which runs the inverse map, not the forward transform again.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```
python phonomir.py [command] [text]
```

If no command is given, `translate` is assumed.

### Commands

| Command | Description |
|---------|-------------|
| `translate` | Forward translation: English → phonemes → swap → respelled output |
| `reverse` | Reverse translation: deciphers translated output back to English |
| `phonemize` | Show the phoneme breakdown for an English word or phrase |
| `spell` | Convert a phoneme string back to its best English spelling |
| `swap` | Apply the rule table directly to a phoneme stream |
| `cache` | Inspect or edit the translation dictionary |

### Options (translate / reverse)

| Flag | Description |
|------|-------------|
| `--rules FILE` | Use a custom rules file (default: `rules/default.txt`) |
| `--cache FILE` | Use a custom cache file |
| `--no-cache` | Skip cache read and write |
| `-i`, `--interactive` | Interactive REPL mode |
| `-v`, `--verbose` | Show phoneme breakdown alongside output |
| `-f FILE`, `-o FILE` | Read from / write to a file |

### Examples

```bash
# Encipher a phrase
$ python phonomir.py translate "hello world"
rertheh howthsh

# Decipher
$ python phonomir.py reverse "rertheh howthsh"
hello world

# Show phoneme breakdown
$ python phonomir.py phonemize "hello"
HH AH0 L OW1

# Interactive mode
python phonomir.py translate -i

# Scan CMU dictionary for words whose translations are also real words
python phonomir.py translate --scan-pairs
```

### Cache subcommands

The translation dictionary caches word pairs to speed up repeat lookups and lets you manually override entries.

```bash
python phonomir.py cache list
python phonomir.py cache get hello
python phonomir.py cache add hello rertheh
python phonomir.py cache remove hello
```

## Rules

Rules are defined in plain-text files as `PHONEME -> PHONEME` mappings. Cycles are computed automatically from the forward map; the reverse map is derived by inverting it.

The default ruleset (`rules/default.txt`) uses three consonant cycles and one vowel cycle:

- **12-cycle** — stops + obstruent fricatives, alternating manner: P→V→T→Z→K→ZH→B→S→D→SH→G→F→P
- **7-cycle** — remaining fricatives + liquids + glides: TH→L→DH→W→HH→R→Y→TH
- **5-cycle** — nasals + affricates: M→CH→N→JH→NG→M
- **15-cycle** — all vowels: IY→AA→EY→AE→UW→AO→IH→OW→EH→UH→AY→OY→AH→ER→AW→IY

Phonemes follow ARPAbet notation. Sounds not listed in any rule pass through unchanged.

To write a custom ruleset, copy `rules/default.txt` and pass it with `--rules`.
