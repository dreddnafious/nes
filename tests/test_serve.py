"""The page's server: ordinary mistakes on its command line are messages, and it serves nothing outside
src/ (beside the page at /). These are the project's own tests, beside the frozen corpus in cases/."""
import http.client
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node") or "node"
SERVE = str(ROOT / "src" / "web" / "serve.ts")


@pytest.mark.parametrize("args", [(), ("--port",), ("--port", "abc"), ("--port", "0"), ("--port", "70000"),
                                  ("--prot", "8080")])
def test_a_bad_command_line_is_a_usage_message(args):
    r = subprocess.run([NODE, SERVE, *args], capture_output=True, text=True, timeout=10)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "--port" in r.stderr, r.stderr
    assert not re.search(r"^\s+at \S", r.stderr, re.M), r.stderr


@pytest.fixture(scope="module")
def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        n = s.getsockname()[1]
    proc = subprocess.Popen([NODE, SERVE, "--port", str(n)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", n), timeout=1).close()
            break
        except OSError:
            time.sleep(0.1)
    yield n
    proc.terminate()
    proc.wait(timeout=10)


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.getheader("content-type"), r.read()


def test_the_page_and_its_typescript_are_served_as_javascript(port):
    status, kind, body = get(port, "/")
    assert status == 200 and kind.startswith("text/html") and b'id="screen"' in body
    status, kind, body = get(port, "/src/web/keyboard.ts")
    assert status == 200 and kind.startswith("text/javascript")
    assert b"Readonly<Record" not in body and b"KEY_MAP" in body, "the types weren't stripped"


@pytest.mark.parametrize("path", ["/package.json", "/src/../package.json", "/src/%2e%2e/package.json",
                                  "/src/..%2fpackage.json", "/src/core/missing.ts", "/roms/LICENSES.md",
                                  "/src/core"])
def test_nothing_outside_src_is_served(port, path):
    status, _, body = get(port, path)
    assert status == 404, (path, status, body[:80])
