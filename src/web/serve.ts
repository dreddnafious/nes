// The page's local server (see cases/CONTRACT-I3.md):
//   node src/web/serve.ts --port <N>
// It serves the page at http://127.0.0.1:<N>/ and the TypeScript it runs (src/web, src/core), with the
// types stripped as each file is served (Node's own module.stripTypeScriptTypes): no build step, and no
// runtime dependency. It listens on 127.0.0.1 only, until SIGTERM or SIGINT.
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { stripTypeScriptTypes } from 'node:module';
import { extname, resolve, sep } from 'node:path';

const USAGE = 'usage: node src/web/serve.ts --port <N>';
const ROOT = resolve(import.meta.dirname, '..', '..');
const SRC = resolve(ROOT, 'src');
const PAGE = resolve(ROOT, 'src', 'web', 'index.html');

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.ts': 'text/javascript; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

function fail(message: string, code = 2): never {
  process.stderr.write(`${message}\n`);
  process.exit(code);
}

function parsePort(argv: string[]): number {
  if (argv.length !== 2 || argv[0] !== '--port') fail(USAGE);
  if (!/^[0-9]+$/.test(argv[1])) fail(`--port: not a number: ${argv[1]}\n${USAGE}`);
  const port = parseInt(argv[1], 10);
  if (port < 1 || port > 65535) fail(`--port: ${port} is not a port (1-65535)`);
  return port;
}

/** The file a URL path names: the page at /, else a file under src/ (nothing outside it). */
function fileFor(urlPath: string): string | null {
  if (urlPath === '/' || urlPath === '/index.html') return PAGE;
  let path: string;
  try {
    path = resolve(ROOT, '.' + decodeURIComponent(urlPath));
  } catch {
    return null;
  }
  if (!path.startsWith(SRC + sep) || !(extname(path) in TYPES)) return null;
  return path;
}

async function body(path: string): Promise<string | Buffer> {
  if (extname(path) !== '.ts') return readFile(path);
  return stripTypeScriptTypes(await readFile(path, 'utf8'), { mode: 'strip' });
}

const port = parsePort(process.argv.slice(2));
const server = createServer(async (req, res) => {
  const urlPath = new URL(req.url ?? '/', 'http://localhost').pathname;
  const path = req.method === 'GET' || req.method === 'HEAD' ? fileFor(urlPath) : null;
  if (path === null) {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }).end('not found\n');
    return;
  }
  try {
    const data = await body(path);
    res.writeHead(200, { 'content-type': TYPES[extname(path)], 'cache-control': 'no-store' });
    res.end(req.method === 'HEAD' ? undefined : data);
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    res.writeHead(code === 'ENOENT' || code === 'EISDIR' ? 404 : 500, { 'content-type': 'text/plain; charset=utf-8' });
    res.end(code === 'ENOENT' || code === 'EISDIR' ? 'not found\n' : `can't serve ${urlPath}: ${String(e)}\n`);
  }
});

server.on('error', (e: NodeJS.ErrnoException) => {
  if (e.code === 'EADDRINUSE') fail(`port ${port} is already in use: pick another with --port`, 1);
  if (e.code === 'EACCES') fail(`port ${port}: permission denied: pick another with --port`, 1);
  fail(`can't listen on 127.0.0.1:${port}: ${e.code ?? e.message}`, 1);
});

server.listen(port, '127.0.0.1', () => {
  process.stdout.write(`serving the page at http://127.0.0.1:${port}/\n`);
});

for (const signal of ['SIGTERM', 'SIGINT'] as const) {
  process.on(signal, () => {
    server.close();
    server.closeAllConnections();
    process.exit(0);
  });
}
