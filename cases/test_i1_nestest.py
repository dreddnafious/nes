"""I1: the CPU against nestest's golden trace (kevtris), run in automation mode from $C000.

The golden log is 8,991 lines. Lines 1-5003 exercise the official opcodes; from line 5004 the
unofficial ones are interleaved. Every promise here is the oracle's, not ours."""
import re
from functools import lru_cache

import pytest

from conftest import cli, oracle

DELTA = pytest.mark.xfail(strict=True, reason="delta")

LINES = 8991
FIRST_UNOFFICIAL = 5004  # 1-based: the first line whose mnemonic is starred in nestest.log
REGS = re.compile(r"A:([0-9A-F]{2}) X:([0-9A-F]{2}) Y:([0-9A-F]{2}) P:([0-9A-F]{2}) SP:([0-9A-F]{2})")
PPU = re.compile(r"PPU:\s*(\d+),\s*(\d+)")
CYC = re.compile(r"CYC:(\d+)")
HEAD = re.compile(r"^([0-9A-F]{4})  ((?:[0-9A-F]{2} ?){1,3})")


def parse(line: str):
    """(pc, bytes, regs, ppu, cyc) from a nestest-format line; None for a part that isn't there."""
    h = HEAD.match(line[:16])
    r, p, c = REGS.search(line), PPU.search(line), CYC.search(line)
    return (h.group(1) if h else None,
            h.group(2).split() if h else None,
            r.groups() if r else None,
            (int(p.group(1)), int(p.group(2))) if p else None,
            int(c.group(1)) if c else None)


@lru_cache(maxsize=1)
def golden():
    return [parse(l) for l in oracle("nestest.log").read_text().splitlines()]


@lru_cache(maxsize=1)
def traced():
    r = cli("trace", oracle("nestest.nes"), "--pc", "C000", "--lines", LINES, "--mem", "0002:2",
            timeout=120)
    lines = (r.stdout or "").splitlines()
    trace = [parse(l) for l in lines if not l.startswith("MEM ")]
    mem = [l for l in lines if l.startswith("MEM ")]
    return r, trace, mem


def first_mismatch(lo: int, hi: int, fields):
    """The first 1-based line in [lo, hi] where the build's trace differs from the golden log in the
    given fields, with both sides; None when they agree on every line."""
    r, trace, _ = traced()
    gold = golden()
    for n in range(lo, hi + 1):
        g = gold[n - 1]
        t = trace[n - 1] if n - 1 < len(trace) else None
        if t is None:
            return n, "missing", f"trace ended at line {len(trace)}; {r!r}"
        for f in fields:
            if g[f] != t[f]:
                return n, g, t
    return None


@DELTA
def test_nestest_official_opcodes_match_the_golden_trace():
    """purpose: every official-opcode instruction nestest runs (golden lines 1-5003) leaves PC, the
    instruction bytes, A, X, Y, P and SP exactly as the golden log says.
    origin: design"""
    assert first_mismatch(1, FIRST_UNOFFICIAL - 1, fields=(0, 1, 2)) is None


@DELTA
def test_nestest_official_opcodes_take_the_golden_cycles():
    """purpose: over golden lines 1-5003, the CPU cycle count and the PPU scanline/dot (three dots per
    CPU cycle) match the golden log on every line: official opcodes are cycle-exact, page-cross and
    branch penalties included.
    origin: design"""
    assert first_mismatch(1, FIRST_UNOFFICIAL - 1, fields=(0, 3, 4)) is None


@DELTA
def test_nestest_unofficial_opcodes_match_the_golden_trace():
    """purpose: from golden line 5004 to the end (8991), where nestest tests the unofficial opcodes
    (NOP variants, LAX, SAX, SBC $EB, DCP, ISB, SLO, RLA, SRE, RRA), PC, bytes and registers match
    the golden log on every line.
    origin: design"""
    assert first_mismatch(FIRST_UNOFFICIAL, LINES, fields=(0, 1, 2)) is None


@DELTA
def test_nestest_unofficial_opcodes_take_the_golden_cycles():
    """purpose: from golden line 5004 to 8991, cycles and PPU scanline/dot match the golden log on
    every line.
    origin: design"""
    assert first_mismatch(FIRST_UNOFFICIAL, LINES, fields=(0, 3, 4)) is None


@DELTA
def test_nestest_reports_no_errors_in_its_result_bytes():
    """purpose: after the 8,991 traced instructions, nestest's own error bytes at $02 and $03 are both
    $00 (it writes the number of the first failed test there), read through the CLI's --mem.
    origin: design"""
    r, _, mem = traced()
    assert mem, f"no MEM line from the CLI; {r!r}"
    assert mem[-1].split(":", 1)[1].split() == ["00", "00"], mem[-1]
