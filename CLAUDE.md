# Phonomir

A reciprocal phonetic substitution cipher ("phonetic mirror"). Words are transformed by swapping each phoneme with its paired counterpart based on articulatory features — voiced/unvoiced for consonants, front/back for vowels.

## Core concept

The cipher is defined in `PHONETIC_MIRROR.md`. Every swap is its own inverse: applying the transform twice returns the original word. Pairs are based on mouth shape and articulation position:

- **Consonants**: voiced ↔ unvoiced (p↔b, t↔d, k↔g, f↔v, s↔z, sh↔zh, ch↔j, th↔th)
- **Nasals/liquids**: m↔n, l↔r, w↔y
- **Vowels**: front ↔ back (ee↔oo, i↔u, a↔o, e↔aw, etc.)

## Key rules

- The cipher operates on **phonemes, not spelling**. "Ignore the spelling. Say the word out loud."
- Every pair is reciprocal — the transform is its own inverse.
- Sounds not in the chart pass through unchanged.

## Project status

Early stage — the substitution chart is defined, no implementation yet.
