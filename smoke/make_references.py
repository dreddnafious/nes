"""Makes the smoke run's title references (smoke/references/<subject>.png): each subject's frame N
(smoke/subjects.json) from the CLI's screenshot (`node src/cli/nes.ts screenshot`, the core's frame
through its palette). The smoke run never runs this: it only reads the committed references, so a build
that breaks the picture can't remake them. Run it by hand when a subject is added or its picture is
meant to change, look at what it made, and commit it:

    .venv/bin/python -B smoke/make_references.py [<subject> ...]
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from probes import PROBES
from smoke import DEFAULTS, GAMES, NODE, ROOT, SMOKE
import json


def main(names: list) -> int:
    params = json.loads((SMOKE / "subjects.json").read_text())
    roms = {p.stem: p for p in sorted(GAMES.glob("*.nes"))}
    with tempfile.TemporaryDirectory() as tmp:
        for name, build in PROBES.values():
            roms[name] = Path(tmp) / f"{name}.nes"
            roms[name].write_bytes(build())
        for name in names or sorted(roms):
            frame = {**DEFAULTS, **params.get(name, {})}["frame"]
            out = SMOKE / "references" / f"{name}.png"
            out.parent.mkdir(exist_ok=True)
            r = subprocess.run([NODE, str(ROOT / "src" / "cli" / "nes.ts"), "screenshot", str(roms[name]),
                                "--frames", str(frame), "--out", str(out)], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"{name}: {r.stderr.strip()}", file=sys.stderr)
                return 1
            print(f"{out.relative_to(ROOT)}: frame {frame}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
