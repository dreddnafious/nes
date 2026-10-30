"""The vendored games carry the notices their licences make redistribution conditional on: a clone of the
repository gets each MIT game's copyright and permission notice and each GPL game's licence text, beside
the ROM (roms/licenses/), and roms/LICENSES.md points at them."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ROOT / "roms" / "LICENSES.md"

# game -> (its licence file, words the full text must hold)
NOTICES = {
    "file-fixers.nes": ("roms/licenses/file-fixers-LICENSE.txt",
                        ["Copyright (c) 2021 Wendel Scardua", "Permission is hereby granted",
                         "The above copyright notice and this permission notice shall be included"]),
    "thwaite.nes": ("roms/licenses/thwaite-LICENSE.txt",
                    ["GNU GENERAL PUBLIC LICENSE", "Version 3, 29 June 2007", "END OF TERMS AND CONDITIONS"]),
}


def test_each_mit_or_gpl_game_has_its_full_licence_beside_it_and_its_row_points_there():
    rows = [ln for ln in LICENSES.read_text().splitlines() if ln.startswith("|")]
    for game, (path, words) in NOTICES.items():
        assert (ROOT / "roms" / "games" / game).is_file(), game
        text = (ROOT / path).read_text()
        missing = [w for w in words if w not in text]
        assert not missing, f"{path} lacks {missing}"
        row = [r for r in rows if f"games/{game}" in r]
        assert len(row) == 1 and path in row[0], f"the row for {game} doesn't point at {path}"
