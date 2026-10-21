"""Folder matching verdicts on real name pairs. Run: python -m pytest tests, or python tests/test_match.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import match  # noqa: E402

# (uploader, SoundCloud title, file name, tag label, verdict). None means a different song.
CASES = [
    # Same song, exact
    ("DJ Q", "Bassline Junkie", "Bassline Junkie.mp3", "DJ Q - (U) Bassline Junkie", "exact"),
    ("Studio Bros", "Malaia", "Malaia (Original Mix).mp3", "Studio Bros - (UI) Malaia (Original Mix)", "exact"),
    ("Local Action", "Finn - Keep Calling (DJ Q Remix)", "Finn - Keep Calling (DJ Q Remix).mp3", "Local Action - (U) Finn - Keep Calling (DJ Q Remix)", "exact"),
    ("Casement", "Bad Habit", "Casement - Bad Habit V2.mp3", "Casement - (U) Bad Habit", "exact"),
    ("Sensa", "Actin' Shady", "Sensa - Steppers Dubs - 04 Actin' Shady.mp3", "Sensa - (LU) Actin' Shady", "exact"),
    ("Sosodality", "Do For Love - SANTO", "SANTO - Do For Love .mp3", "Sosodality - (L) Do For Love - SANTO", "exact"),
    ("NAUTIKA", "Blur - Song 2 (Nautika Bootleg) [Free Download]", "Blur - Song 2 (Nautika Bootleg).mp3", "(U) Blur - Song 2 (Nautika Bootleg)", "exact"),
    ("Gilla", "Push 2 Start (Gilla Remix)", "108_Bm PUSH TO START (GILLA REMIX).mp3", "(L) PUSH TO START (GILLA REMIX)", "exact"),
    ("POP G", "JETSKI MALAIA TO BRASIL (POP G EDIT)", "JET SKI MALAIA TO BRASIL (POP G EDIT).mp3", "(D) JET SKI MALAIA TO BRASIL (POP G EDIT)", "exact"),
    ("BUCKS", "The Weeknd - Starboy (Bucks & Miciko Amapiano Edit)", "Starboy (Bucks & Miciko Amapiano Edit).mp3", "The Weekend - (LU) Starboy (Bucks & Miciko Amapiano Edit)", "exact"),
    ("ToloMarvelous", "Bass Down Low", "Bass Down Low.mp3", "Tolo Marvelous - (U) Bass Down Low", "exact"),
    ("5HOURS", "Hay Lupita (5HOURS & Valeria Panza Remix)", "Lomiiel - Hay Lupita (5HOURS & Valeria Panza Remix).mp3", "(U) Lomiiel - Hay Lupita (5HOURS & Valeria Panza Remix)", "exact"),
    # Probably the same, needs a look
    ("Disrupta", "JEREMIH - BIRTHDAY SEX (DISRUPTA BOOTLEG) [FREE DOWNLOAD]", "JEREMIAH - BIRTHDAY SEX (DISRUPTA BOOTLEG).mp3", "(L) JEREMIAH - BIRTHDAY SEX (DISRUPTA BOOTLEG)", "title"),
    ("CRRDR", "CRRDR x Bomfunk MC's - Freestyler  | FREE DOWNLOAD", "Bomfunk Mcs - Freestyler.mp3", "Bomfunk Mc's - (U) Freestyler", "title"),
    # Different versions or songs, shown only as possible
    ("Dj Nané", "Rihanna - SOS (Dj Nané Speed Garage Edit) [FREE DOWNLOAD]", "Rihanna - S&M (Dj Nané Speed Garage Edit).mp3", "(D) Rihanna - S&M (Dj Nané Speed Garage Edit)", "fuzzy"),
    ("MARCHBORN", "DIDNT CHA KNOW DUB", "MARCHBORN - DIDNT CHA KNOW (EXTENDED MIX).mp3", "(L) MARCHBORN - DIDNT CHA KNOW (EXTENDED MIX)", "fuzzy"),
    ("SUMWEST", "i am me (sumwest edit)", "i am (sumwest edit).mp3", "(D) i am (sumwest edit)", "fuzzy"),
    # Different songs
    ("Drew", "CLOSER", "Closer.mp3", "Ne-Yo - Closer", None),
    ("Jack Blom", "Paradise", "02 - Paradise.mp3", "Sade - Paradise", None),
    ("Sangarang", "Never Enough (Extended Mix)", "Never Enough.mp3", "Groove Theory - (L) Groove Theory - Never Enough", None),
    ("SOULECTION", "Soulection Radio Show #157", "Soulection 700_EXPLICIT_SOUNDCLOUD.mp3", "Soulection Radio - Show #700", None),
    ("Nuno Beats", "NUNO BEATS - JET 2", "NUNO BEATS - JUMP!!!.mp3", "Nuno Beats - (3) NUNO BEATS - JUMP!!!", None),
    ("Andrew Son", "Jumpman (Andrew Son Remix)", "Jump (Andrew Son Remix).mp3", "Andrew Son - (U) Jump (Andrew Son Remix)", None),
    ("sonny makepeace", "lost..", "Lost.mp3", "(LD) Shaggy - Lost", None),
]


def test_cases():
    bad = []
    for artist, name, filename, tag, want in CASES:
        got = match.judge({"artist_name": artist, "name": name}, filename, tag)
        if got != want:
            bad.append(f"{artist} - {name} <-> {filename}: want {want}, got {got}")
    assert not bad, "\n".join(bad)


def test_one_word_title_needs_an_artist():
    assert match.judge({"artist_name": "CRRDR", "name": "SEX"}, "Sex.mp3", "Sex") == "fuzzy"


def test_match_filenames_drops_different_songs():
    tracks = [{"id": 1, "artist_name": "Drew", "name": "CLOSER"},
              {"id": 2, "artist_name": "DJ Q", "name": "Bassline Junkie"}]
    got = match.match_filenames(tracks, ["Closer.mp3", "Bassline Junkie.mp3"], 85,
                                tags=["Ne-Yo - Closer", "DJ Q - (U) Bassline Junkie"])
    assert [(m["track_id"], m["kind"], m["score"]) for m in got] == [(2, "exact", 100)]


if __name__ == "__main__":
    test_cases()
    test_one_word_title_needs_an_artist()
    test_match_filenames_drops_different_songs()
    print("ok")
