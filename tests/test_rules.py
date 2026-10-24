"""The yaml blocks of pronunciation-rules.md, as the scorer reads them."""
import re

import yaml

from app.rules import RULES_PATH, Check, check_detectable, load, parse, undetectable


def test_every_block_parses_into_rules():
    r = load()
    assert {"Q", "K", "TH", "DH", "ZH", "J"} <= set(r.archiphonemes)
    assert r.dialects == ["urban", "rural"] and len(r.checks) >= 30
    assert r.check("ħ", "χ").severity == 3            # written χ, stored by its base() name x
    assert r.margin(["ZH", "ðˤ"], "z") == 0.7         # ZH has no margin of its own: the written phoneme's applies


def test_every_heard_symbol_has_a_hebrew_label():
    text = RULES_PATH.read_text(encoding="utf-8")
    heard = {str(h) for block in re.findall(r"```yaml\n(.*?)```", text, re.S)
             for doc in re.split(r"^---$", block, flags=re.M) if doc.strip()
             for c in (yaml.safe_load(doc) or {}).get("checks", []) for h in c["heard"]}
    assert heard - set(load().heard_labels) == set()


def test_correct_realisations_per_letter():
    r = load()
    assert r.members("Q") == ["ʔ", "k", "q"]                       # Bedouin g is off by default
    assert r.members("TH") == ["t", "θ"] and "s" in r.members("TH", written="s")   # s only in learned words
    assert r.dialects_of("Q", "k") == {"rural"} and r.dialects_of("K", "k") == {"urban", "rural"}


def test_undetectable_rules_are_reported():
    r = load()
    everything = lambda p: True
    assert undetectable(Check("x", "ħ", "ħ", 1, ""), r, everything) == "same_sound"
    assert undetectable(Check("x", "Q", "k", 1, ""), r, everything) == "accepted"
    assert undetectable(Check("x", "ħ", "ǀ", 1, ""), r, lambda p: p != "ǀ") == "heard_not_in_model"
    report = check_detectable(r, everything)
    assert report["pharyngeal_h"]["detectable"]
    # base() drops ː, but a long vowel said short is measured by its duration (3.8)
    assert report["vowel_length"]["detectable"] and not report["vowel_length"]["missing"]
    bad = parse("```yaml\nid: bad\ntitle: bad\nchecks:\n  - {target: aː, heard: [a], severity: 1, feedback: x}\n```\n")
    assert not check_detectable(bad, everything)["bad"]["detectable"]
