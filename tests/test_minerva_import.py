from scripts.import_minerva import phrases


def test_minerva_import_takes_every_reviewed_line_from_the_metadata():
    made = phrases()
    keys = [p[2]["key"] for p in made]
    assert len(keys) == len(set(keys))
    assert all(s["start"] < s["end"] and p["variants"] == [(s["ar"], s["ipa"])] for _, s, p in made)
    pilot = phrases({"U8aBRd3-wlM", "OuAwxXUZBU4", "V2y6LqnoXTo"})
    assert len(pilot) == 70 and pilot[0][2]["key"] == "minerva:U8aBRd3-wlM:01"
