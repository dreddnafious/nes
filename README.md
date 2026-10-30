# nes

A NES emulator in TypeScript. It has a pure core (`src/core`), run headless under Node for the tests and in
the browser for play, and a headless CLI (`src/cli`). It is being built increment by increment against the
test ROMs; see `cases/CONTRACT.md` for the surface the tests drive.

## Setup
- Node 24 or later runs the TypeScript directly; there is no build step.
- Install the type-checker: `npm install` (there are no runtime dependencies).
- Set up the Python test rig:

  ```sh
  uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
  .venv/bin/python -m playwright install chromium    # the page's cases run in headless Chromium
  ```

## Tests
- `npm test` runs the corpus (`cases/`) and the project's own tests (`tests/`).
- `npm run typecheck` checks the source under strict mode.
- The test ROMs aren't in git. The corpus fetches each one by URL and SHA-256 on first use (see
  `cases/oracles.json` and `roms/LICENSES.md`).

## Play in the browser
```sh
node src/web/serve.ts --port 8080
```
Then open http://127.0.0.1:8080/ and pick a `.nes` file. The server listens on 127.0.0.1 only, and strips
the TypeScript's types as it serves each file, so there's still no build step.

| key | button |
|---|---|
| arrows | the pad |
| X | A |
| Z | B |
| Enter | Start |
| Right Shift | Select |

**Reset** is the console's reset button (RAM is kept). **Power** turns the same game off and on (a fresh
console; a battery save is kept).

## CLI
```sh
node src/cli/nes.ts trace <rom.nes> [--pc <hex>] [--lines <n>] [--mem <hexaddr>:<n>]...
node src/cli/nes.ts run   <rom.nes> [--frames <n>] [--mem <hexaddr>:<n>]...
node src/cli/nes.ts screenshot <rom.nes> [--frames <n>] --out <file.png>
```
