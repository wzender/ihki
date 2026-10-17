from scripts.import_minerva import PILOT, phrases


def test_minerva_pilot_manifest_has_exactly_the_reviewed_three_videos_and_70_lines():
    assert [v.youtube_id for v in PILOT] == ["U8aBRd3-wlM", "OuAwxXUZBU4", "V2y6LqnoXTo"]
    assert [len(v.lines) for v in PILOT] == [15, 22, 33]
    made, _ = phrases()
    assert len(made) == 70
    assert len({p[4]["key"] for p in made}) == 70
    assert all(p[2] < p[3] and p[4]["variants"] for p in made)
