"""I1: blargg's CPU test ROMs, through the core API and the frozen driver (cases/drive.ts).

The cases' data is i1_blargg_cpu.yaml; each case there carries its own promise and delta mark.
A ROM that hangs past its frame budget, crashes, or never signs $6001 counts as a fail, never a
skip: the denominator is the fixed list (variant J4e)."""
from pathlib import Path

import pytest
import yaml

from conftest import drive, oracle

DELTA = pytest.mark.xfail(strict=True, reason="delta")
DATA = yaml.safe_load((Path(__file__).parent / "i1_blargg_cpu.yaml").read_text())["cases"]
MAX_FRAMES = 3600  # 60 s of emulated time; the longest of these ROMs needs well under half that


def params():
    return [pytest.param(c, id=c["id"], marks=[DELTA] if c.get("delta") is True else [])
            for c in DATA]


@pytest.mark.parametrize("case", params())
def test_blargg_cpu_rom_reports_passed(case):
    """Each blargg CPU ROM, run headless until it reports, signs $6001-$6003 with DE B0 61, ends with
    status $00, and its text at $6004 says "Passed". A $81 status is answered with a Reset press
    after at least 6 frames, as the ROM asks."""
    res = drive({"mode": "blargg", "rom": str(oracle(case["id"])), "maxFrames": MAX_FRAMES},
                timeout=180)
    assert res.get("ok") and res.get("done"), f"{case['id']}: no result: {res}"
    assert res["signature"] is True
    assert res["status"] == 0, f"{case['id']}: status {res['status']}: {res['text']!r}"
    assert "passed" in res["text"].lower(), res["text"]
