"""The MMC3's PRG-RAM protect register ($A001) on NES 2.0 files: honoured on submapper 0 (the MMC3)
only. Submapper 1 (the MMC6, StarTropics) gives $A001 other meanings, so there the RAM stays on, as on
an iNES 1.0 file. These are the project's own tests, beside the frozen corpus in cases/."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node") or "node"
CLI = str(ROOT / "src" / "cli" / "nes.ts")


def mmc3_rom(tmp_path, submapper: int) -> str:
    """A NES 2.0 mapper-4 file (32 KiB PRG, 8 KiB CHR, 1 KiB PRG-RAM) whose program writes $A001 = $F0,
    the MMC6's "both halves readable and writable" (on the MMC3: enabled, write-protected), then $5A at
    $6000 and $A5 at $7001, and spins."""
    code = bytes([
        0x78,                    # SEI
        0xA9, 0xF0, 0x8D, 0x01, 0xA0,  # LDA #$F0 / STA $A001
        0xA9, 0x5A, 0x8D, 0x00, 0x60,  # LDA #$5A / STA $6000
        0xA9, 0xA5, 0x8D, 0x01, 0x70,  # LDA #$A5 / STA $7001
        0x4C, 0x10, 0xE0,        # JMP $E010 (itself)
    ])
    prg = bytearray(0x8000)
    prg[0x6000:0x6000 + len(code)] = code
    prg[0x7FFA:0x8000] = bytes([0x10, 0xE0, 0x00, 0xE0, 0x10, 0xE0])
    header = bytes([0x4E, 0x45, 0x53, 0x1A, 2, 1, 0x40, 0x08, submapper << 4, 0, 0x04, 0, 0, 0, 0, 0])
    p = tmp_path / f"mmc3-sub{submapper}.nes"
    p.write_bytes(header + bytes(prg) + bytes(0x2000))
    return str(p)


def ram_after(path: str) -> str:
    r = subprocess.run([NODE, CLI, "run", path, "--frames", "2", "--mem", "6000:2"], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.lower()


@pytest.mark.parametrize("submapper", [1, 3])
def test_a001_doesnt_write_protect_the_ram_on_other_submappers(tmp_path, submapper):
    out = ram_after(mmc3_rom(tmp_path, submapper))
    assert "5a a5" in out, out


def test_a001_write_protects_the_ram_on_the_mmc3(tmp_path):
    out = ram_after(mmc3_rom(tmp_path, 0))
    assert "5a a5" not in out, out
