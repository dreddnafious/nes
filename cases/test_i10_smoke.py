"""I10: the smoke run (J5), "a smoke run I can see". Each case runs the one command the way J5's consumer
does: in a checkout of this repository (a temporary git worktree of HEAD, clean; or HEAD with the working
tree laid over it, for the copies a case breaks on purpose), `npm run smoke`, then the exit code and the
report in `out/smoke/` read (CONTRACT-I10.md). The broken builds are patches to that copy, never a flag that
ships (Decided); their rows are in `cases/i10_cases.yaml`. Every process is bounded in time and every
checkout removed after.

Not a case: J5's "check out `main` after the merge" and "the climb's merge step runs it against `main` and
shows the report to Jason" are the merge gate's. What a case can pin of them is pinned here: the command
run in a clean checkout of the current commit, and the report naming that commit and whether the tree was
dirty, with a pointer to the latest."""
import json
import re
import threading

import pytest

from i10_support import (FULL_RUN_S, PICTURE_CHECKS, REGISTRY_TODAY, Checkout, break_build, head_sha,
                         latest, params, png_size, report_of, runs_in, smoke, whole, NODE)
from conftest import ROOT, run

DELTA = pytest.mark.xfail(strict=True, reason="delta")
ONE_MAPPER_S = 180          # a run narrowed to one mapper: bounded well inside the full run's 5 minutes


# ---------------------------------------------------------------- the core's registry (J5c)

def test_the_core_s_registry_is_exactly_the_mappers_it_accepts(tmp_path):
    """purpose: the smoke run's mapper list comes from the core's registry, never a hand list (J5c), so the
    registry must be the truth: `MAPPERS` from src/core/index.ts is ascending, each number once, and is
    exactly the set of iNES mapper numbers 0-255 for which `new NES(bytes)` accepts a plain iNES 1.0 file
    (32 KiB PRG-ROM, 8 KiB CHR-ROM): every other number is refused. It holds at least 0, 1, 2, 3, 4 and 7,
    the mappers the first climb shipped (I9 added none).
    origin: design"""
    script = tmp_path / "accept.ts"
    script.write_text(f"""const core = await import({str((ROOT / 'src' / 'core' / 'index.ts').as_uri())!r});
const accepted: number[] = [];
for (let m = 0; m < 256; m++) {{
  const b = new Uint8Array(16 + 32768 + 8192);
  b.set([0x4e, 0x45, 0x53, 0x1a, 2, 1, (m & 15) << 4, m & 0xf0]);
  try {{ new core.NES(b); accepted.push(m); }} catch (e) {{ if (!(e instanceof core.RomError)) throw e; }}
}}
console.log(JSON.stringify({{ mappers: Array.isArray(core.MAPPERS) ? Array.from(core.MAPPERS) : null, accepted }}));
""")
    r = run([NODE, str(script)], timeout=120)
    assert r.returncode == 0 and r.stdout.strip(), repr(r)
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["mappers"] is not None, "src/core/index.ts exports no MAPPERS array (CONTRACT-I10)"
    assert got["mappers"] == sorted(set(got["mappers"])), f"MAPPERS {got['mappers']} isn't ascending, each once"
    assert got["mappers"] == got["accepted"], \
        f"MAPPERS {got['mappers']} isn't the set the core accepts {got['accepted']}"
    assert set(REGISTRY_TODAY) <= set(got["mappers"]), f"MAPPERS {got['mappers']} lacks one of {REGISTRY_TODAY}"


# ---------------------------------------------------------------- J5 on a good build, in a clean checkout

@pytest.fixture(scope="module")
def clean_run():
    """The whole smoke run (no --mapper) in a clean git worktree of HEAD, once for the module: the run, its
    report's directory (or None), the registry the checkout's core exports, and git status before and after."""
    with Checkout(clean=True) as co:
        before = co.status()
        res = smoke(co.path, timeout=FULL_RUN_S + 60)
        d = latest(co.path)
        rep = None
        try:
            rep = report_of(co.path, res)
        except AssertionError:
            pass
        yield {"run": res, "report": rep, "dir": d, "registry": co.registry(), "before": before,
               "after": co.status(), "path": co.path, "head": head_sha()}


def good_report(clean_run):
    res, rep = clean_run["run"], clean_run["report"]
    assert rep is not None, f"the smoke run left no report in out/smoke/latest ({res!r})"
    assert isinstance(clean_run["registry"], list), f"the checkout's core exports no MAPPERS: {clean_run['registry']}"
    return res, rep


def test_j5_a_smoke_run_i_can_see(clean_run):
    """purpose: J5 walked through as its consumer does it, on the commit HEAD names, checked out clean:
      1. one command, `npm run smoke`, with no flag;
      2. it serves the page on a free local port and drives headless Chromium through the real entry points
         (the file input and the game menu, the keyboard, the canvas, Reset and Power, the audio output);
      3. on every mapper the core's registry lists (at least 0, 1, 2, 3, 4 and 7), each covered by a vendored
         game or a probe, every vendored game loaded through its menu entry too;
      4. it exits 0 within 5 minutes, and leaves its report on disk: out/smoke/latest leads to a directory
         named with the commit's first 7 hex digits holding report.md and report.json, whose every check
         PASSED (a probe's game-menu N/A), whose mappers are the registry, and which names the port it
         served on, the commit (HEAD) and a clean tree; its last line of output names report.md.
    origin: design"""
    res, rep = good_report(clean_run)
    assert res.returncode == 0 and not res.timed_out, f"the smoke run failed on a good build: {res!r}"
    assert res.seconds < FULL_RUN_S, f"the smoke run took {res.seconds:.0f} s (J5: within {FULL_RUN_S} s)"
    j = rep.json
    assert isinstance(j.get("seconds"), (int, float)) and j["seconds"] < FULL_RUN_S, f"seconds: {j.get('seconds')}"
    problems = whole(rep, clean_run["registry"])
    assert not problems, problems
    assert set(REGISTRY_TODAY) <= set(j["mappers"]), f"mappers {j['mappers']} lack one of {REGISTRY_TODAY}"
    bad = [r for r in rep.checks if r["result"] != "PASSED" and not (r["result"] == "N/A" and r["check"] == "game-menu")]
    assert not bad, f"checks that didn't pass on a good build: {bad}"
    assert j["result"] == "PASSED" and j["failures"] == 0, (j["result"], j["failures"])
    assert any(s.get("menu") for s in rep.subjects()), "no subject was loaded through the game menu"
    assert isinstance(j.get("port"), int) and 0 < j["port"] < 65536, f"port: {j.get('port')}"
    assert j.get("commit") == clean_run["head"] and j.get("dirty") is False, (j.get("commit"), j.get("dirty"))
    assert clean_run["head"][:7] in rep.dir.name, f"the run's directory {rep.dir.name} doesn't name the commit"
    last = [ln for ln in res.stdout.splitlines() if ln.strip()][-1:]
    assert last and "report.md" in last[0] and rep.dir.name in last[0], f"the last line of output: {last}"


def test_the_report_is_whole_and_its_result_is_the_exit_code(clean_run):
    """purpose: the report's own invariants (J5b, J5c, Decided), on the good build's full run:
      - its mappers are the core's registry, all of it, and `only` is null;
      - every mapper has one `covered` row, and every subject on it one row of each of the eight subject
        checks (file-input, game-menu, title, key, sound, reset, power, loaded-once), none left out;
      - every vendored game is a menu subject on its own mapper;
      - N/A only on a probe's game-menu; a row whose prerequisite didn't pass is NOT RUN;
      - the counts tally the rows, failures = FAILED + NOT RUN, and result PASSED exactly when a row passed
        and none failed or didn't run; and the command's exit code is 0 exactly when the result is PASSED.
    origin: design"""
    res, rep = good_report(clean_run)
    problems = whole(rep, clean_run["registry"])
    assert not problems, problems
    assert (res.returncode == 0) == (rep.json["result"] == "PASSED"), \
        f"exit {res.returncode} with result {rep.json['result']}"


def test_the_markdown_twin_names_each_check_and_its_result(clean_run):
    """purpose: "a report that names each check and its result, kept as a file", for a person: report.md
    names the commit (its 40 hex digits), the tree as clean or dirty, the run's result on a line that says
    "result", and has, in the JSON's order, one line per row of checks naming its mapper, its subject (where it
    has one), the check and the result.
    origin: design"""
    _, rep = good_report(clean_run)
    md, j = rep.md, rep.json
    assert j["commit"] in md, "report.md doesn't name the commit"
    assert ("dirty" if j["dirty"] else "clean") in md.lower(), "report.md doesn't say whether the tree was clean"
    assert any("result" in ln.lower() and j["result"] in ln for ln in md.splitlines()), \
        f"report.md has no line giving the result {j['result']}"
    lines, at, missing = md.splitlines(), 0, []
    for r in rep.checks:
        tok = re.compile(rf"(?<![\w.]){r['mapper']}(?![\w.])")
        want = [r["check"], r["result"]] + ([r["subject"]] if r.get("subject") else [])
        k = next((i for i in range(at, len(lines)) if all(w in lines[i] for w in want) and tok.search(lines[i])), None)
        if k is None:
            missing.append((r["mapper"], r.get("subject"), r["check"], r["result"]))
        else:
            at = k + 1
    assert not missing, f"rows report.md doesn't name in order: {missing[:8]}"


def test_each_picture_check_saves_the_frame_it_judged(clean_run):
    """purpose: the frame PNGs saved beside the report (J5's settled fork): every game-menu, title, key, reset
    and power row that ran names in `png` a file in the run's own directory that is a 256 x 240 PNG.
    origin: design"""
    _, rep = good_report(clean_run)
    ran = [r for r in rep.checks if r["check"] in PICTURE_CHECKS and r["result"] in ("PASSED", "FAILED")]
    assert ran, "no picture check ran"
    bad = []
    for r in ran:
        name = r.get("png")
        ok = isinstance(name, str) and "/" not in name and png_size(rep.dir / name) == (256, 240)
        if not ok:
            bad.append((r["mapper"], r.get("subject"), r["check"], name))
    assert not bad, f"rows without a 256x240 PNG beside the report: {bad[:8]}"


def test_a_clean_checkout_is_reported_clean_and_left_clean(clean_run):
    """purpose: J5e and J5l's clean side: run in a clean checkout, the report names the commit the checkout
    is at (HEAD, 40 hex digits) and says the tree was not dirty; and the run writes nothing git tracks
    (references are read, never made or remade by a run): the checkout is as clean after it as before.
    origin: design"""
    res, rep = good_report(clean_run)
    assert clean_run["before"] == "", f"the clean checkout wasn't clean before the run: {clean_run['before']!r}"
    assert rep.json.get("commit") == clean_run["head"], f"commit {rep.json.get('commit')} != HEAD {clean_run['head']}"
    assert rep.json.get("dirty") is False, f"dirty: {rep.json.get('dirty')} on a clean checkout"
    assert clean_run["after"] == "", f"the run changed what git tracks: {clean_run['after']!r}"


# ---------------------------------------------------------------- J5a: nothing checked is a failure

def test_a_run_that_checks_nothing_fails():
    """purpose: J5a: "all passed" is never vacuous. Asked only for a mapper the registry doesn't have (66),
    the run has zero checks: it exits non-zero, and still writes its report, which says FAILED with no row
    passed, the registry in `mappers` and [66] in `only`.
    origin: design"""
    with Checkout() as co:
        res = smoke(co.path, "--mapper", 66, timeout=ONE_MAPPER_S)
        rep = report_of(co.path, res)
        assert res.returncode not in (0, None), f"a run with zero checks exited {res.returncode}"
        j = rep.json
        assert j.get("result") == "FAILED" and (j.get("counts") or {}).get("PASSED") == 0, \
            (j.get("result"), j.get("counts"))
        assert j.get("only") == [66] and 66 not in (j.get("mappers") or []), (j.get("only"), j.get("mappers"))


# ---------------------------------------------------------------- broken on purpose

@pytest.mark.parametrize("row", params("broken", DELTA))
def test_a_build_broken_on_purpose_fails_the_check_it_breaks(row):
    """purpose: the smoke run's oracle, cross-checked (I10's roadmap): a copy of the checkout broken on
    purpose by one patch (i10_cases.yaml: a mapper the core refuses, a number the registry adds with no
    subject, the file input dead, an empty game menu, a blank canvas, a wrong palette, a key unmapped,
    silence at the output, a sound that never resumes under the autoplay policy, a dead Reset, a Power
    that keeps RAM), run with `npm run smoke -- --mapper <n>`, exits non-zero; its report is whole; the
    check the patch breaks is FAILED (on every subject of the mapper where the row says `every`), and it is
    the only check FAILED: what depends on it is NOT RUN, everything else PASSED or N/A (only the root cause
    is FAILED, Decided).
    origin: design"""
    with Checkout() as co:
        break_build(co, row["patch"], row.get("arg"))
        res = smoke(co.path, "--mapper", row["mapper"], timeout=ONE_MAPPER_S)
        rep = report_of(co.path, res)
        assert res.returncode not in (0, None), f"the broken build ({row['id']}) passed the smoke run: {res!r}"
        registry = co.registry()
        assert isinstance(registry, list), f"the copy's core exports no MAPPERS: {registry}"
        problems = whole(rep, registry, only=[row["mapper"]])
        assert not problems, problems
        failed = rep.failed()
        want = [r for r in rep.checks if r["mapper"] == row["mapper"] and r["check"] == row["check"]]
        assert any(r["result"] == "FAILED" for r in want), \
            f"{row['check']} wasn't FAILED for mapper {row['mapper']}: {[(r.get('subject'), r['result']) for r in want]}"
        if row.get("every"):
            assert all(r["result"] == "FAILED" for r in want), \
                f"{row['check']} didn't fail on every subject: {[(r.get('subject'), r['result']) for r in want]}"
        other = [(r["mapper"], r.get("subject"), r["check"]) for r in failed if r["check"] != row["check"]]
        assert not other, f"checks FAILED besides the root cause {row['check']}: {other}"


# ---------------------------------------------------------------- J5e, J5l: the checkout as it stands

def test_a_dirty_checkout_is_reported_dirty_and_served_as_it_stands_between_runs():
    """purpose: J5e and J5l: the report describes the code it served, never a stale build. In a copy of the
    checkout with an uncommitted change, `npm run smoke -- --mapper 3` passes and its report says the commit
    (HEAD) and that the tree was dirty. Then the same copy is broken (the core refuses mapper 3) and the whole
    run is made again: it exits non-zero, mapper 3's file-input is FAILED with everything after it NOT RUN,
    and every other mapper is still checked and passes (only the root cause is FAILED).
    origin: design"""
    with Checkout() as co:
        co.touch_tracked()
        first = smoke(co.path, "--mapper", 3, timeout=ONE_MAPPER_S)
        rep = report_of(co.path, first)
        assert first.returncode == 0, f"the dirty but working copy failed: {first!r}"
        assert rep.json.get("commit") == head_sha() and rep.json.get("dirty") is True, \
            (rep.json.get("commit"), rep.json.get("dirty"))
        co.refuse_mapper(3)
        second = smoke(co.path, timeout=FULL_RUN_S + 60)
        rep2 = report_of(co.path, second)
        assert rep2.dir != rep.dir, "the second run didn't make its own report"
        assert second.returncode not in (0, None), f"the patched copy passed: the build served was stale ({second!r})"
        registry = co.registry()
        assert isinstance(registry, list), registry
        problems = whole(rep2, registry)
        assert not problems, problems
        failed = [(r["mapper"], r.get("subject"), r["check"]) for r in rep2.failed()]
        assert failed and all(m == 3 and c == "file-input" for m, _, c in failed), f"FAILED: {failed}"
        others = [r for r in rep2.checks if r["mapper"] != 3 and r["result"] not in ("PASSED", "N/A")]
        assert not others, f"other mappers' checks that didn't pass: {others[:8]}"


# ---------------------------------------------------------------- J5b: Chromium killed

def test_chromium_killed_mid_run_fails_the_run_with_every_check_accounted_for():
    """purpose: J5b: Chromium crashes in the middle of the whole run (every Chromium process under it killed
    with SIGKILL once a check has passed). The run still ends, within its bound, non-zero; its report is
    whole (no row left out: every check that didn't run is listed NOT RUN and counted as a failure), says
    FAILED, and keeps what had passed before the crash.
    origin: design"""
    with Checkout() as co:
        res = smoke(co.path, timeout=FULL_RUN_S + 60, kill_chromium_after="PASSED")
        assert res.killed, f"no Chromium ran under the smoke run after a check passed: {res!r}"
        assert not res.timed_out, f"the run didn't end after Chromium was killed: {res!r}"
        assert res.returncode != 0, f"the run exited 0 though Chromium was killed: {res!r}"
        rep = report_of(co.path, res)
        registry = co.registry()
        assert isinstance(registry, list), registry
        problems = whole(rep, registry)
        assert not problems, problems
        j = rep.json
        assert j["result"] == "FAILED" and j["failures"] >= 1, (j["result"], j["failures"])
        assert j["counts"]["PASSED"] >= 1, f"nothing that passed before the crash was kept: {j['counts']}"


# ---------------------------------------------------------------- J5d, J5m: runs at once, runs in a row

def test_two_runs_at_once_and_one_after_keep_three_reports_and_latest_is_the_last():
    """purpose: J5d and J5m: two runs of `npm run smoke -- --mapper 0` started together in one checkout both
    pass, each on its own port, each with its own directory under out/smoke/ (named by the commit, so
    neither overwrites the other); `latest` leads to one of them. A third run after them makes a third
    directory, keeps the first two, and `latest` then leads to the third.
    origin: design"""
    with Checkout() as co:
        got = {}

        def go(k):
            got[k] = smoke(co.path, "--mapper", 0, timeout=ONE_MAPPER_S + 60)
        threads = [threading.Thread(target=go, args=(k,)) for k in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert got["a"].returncode == 0 and got["b"].returncode == 0, f"runs at once: {got['a']!r} {got['b']!r}"
        two = runs_in(co.path)
        assert len(two) == 2, f"two runs at once left {len(two)} report directories: {[p.name for p in two]}"
        sha7 = head_sha()[:7]
        assert all(sha7 in p.name for p in two), [p.name for p in two]
        reports = [json.loads((p / "report.json").read_text()) for p in two]
        assert all(j.get("result") == "PASSED" for j in reports), [j.get("result") for j in reports]
        ports = [j.get("port") for j in reports]
        assert ports[0] != ports[1], f"both runs served on port {ports[0]}"
        assert latest(co.path) in two, f"latest leads to {latest(co.path)}, not one of {two}"
        third = smoke(co.path, "--mapper", 0, timeout=ONE_MAPPER_S)
        assert third.returncode == 0, repr(third)
        three = runs_in(co.path)
        new = [p for p in three if p not in two]
        assert len(three) == 3 and len(new) == 1, f"after a third run: {[p.name for p in three]}"
        assert latest(co.path) == new[0], f"latest leads to {latest(co.path)}, not the last run {new[0]}"
