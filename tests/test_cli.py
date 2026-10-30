"""The CLI's handling of ordinary mistakes: a mistyped number, a missing file, a reader that stops early.
These are the project's own tests, beside the frozen corpus in cases/."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node") or "node"
CLI = str(ROOT / "src" / "cli" / "nes.ts")


@pytest.fixture
def rom(tmp_path):
    """A 16 KiB NROM program that spins at $C000 (JMP $C000), with every vector pointing there."""
    prg = bytearray(0x4000)
    prg[0:3] = bytes([0x4C, 0x00, 0xC0])
    prg[0x3FFA:0x4000] = bytes([0x00, 0xC0] * 3)
    p = tmp_path / "spin.nes"
    p.write_bytes(b"NES\x1a\x01\x01" + bytes(10) + bytes(prg) + bytes(0x2000))
    return str(p)


def cli(*args, timeout=30):
    return subprocess.run([NODE, CLI, *args], capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize("args", [
    ("run", "--frames", "12x"),
    ("run", "--frames", "1e3"),
    ("trace", "--lines", "8,991"),
    ("run", "--mem", "6000-6010"),
    ("trace", "--pc", "C0zz"),
])
def test_a_number_with_junk_after_it_is_refused(rom, args):
    command, *rest = args
    r = cli(command, rom, *rest)
    assert r.returncode == 2, (r.returncode, r.stdout[-200:], r.stderr)
    assert "not a" in r.stderr and "number" in r.stderr, r.stderr


def test_plain_numbers_are_accepted(rom):
    r = cli("trace", rom, "--pc", "C000", "--lines", "3", "--mem", "$0000:2")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) == 4 and lines[0].startswith("C000  4C 00 C0") and lines[-1] == "MEM 0000: 00 00"


def test_a_missing_rom_file_is_a_message_not_a_stack_trace(tmp_path):
    r = cli("run", str(tmp_path / "no-such.nes"))
    assert r.returncode == 2
    assert "can't read the file" in r.stderr and "ENOENT" in r.stderr
    assert "at " not in r.stderr, r.stderr  # no stack frames


def test_a_reader_that_stops_early_is_not_an_error(rom):
    cmd = f"'{NODE}' '{CLI}' trace '{rom}' --lines 200000 | head -n 1"
    r = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], capture_output=True, text=True, timeout=60)
    assert r.stdout.startswith("C000  4C 00 C0"), r.stdout
    assert "EPIPE" not in r.stderr, r.stderr
    assert r.returncode == 0, r.stderr


def test_a_screenshot_that_cant_be_written_is_a_message_not_a_stack_trace(rom, tmp_path):
    r = cli("screenshot", rom, "--frames", "1", "--out", str(tmp_path / "no-such-dir" / "shot.png"))
    assert r.returncode == 2
    assert "can't write the file" in r.stderr and "ENOENT" in r.stderr
    assert "at " not in r.stderr, r.stderr  # no stack frames


def test_out_is_refused_on_commands_other_than_screenshot(rom, tmp_path):
    r = cli("run", rom, "--out", str(tmp_path / "shot.png"))
    assert r.returncode == 2 and "--out is only for screenshot" in r.stderr, r.stderr
    assert not (tmp_path / "shot.png").exists()
