// Offline, full-file syntax proof. Never loads target code or project config.
import { pathToFileURL } from 'node:url';
import { createInterface } from 'node:readline';

const [major, minor] = process.versions.node.split('.').map(Number);
if (!((major === 22 && minor >= 18) || (major === 24 && minor >= 11) || major > 24)) {
  process.exit(2);
}
const { parse } = await import(pathToFileURL(process.argv[2]).href);
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
emit({ ready: true, version: '8.0.8', policy: 'module-bindings-v1' });

function bindings(request) {
  const plugins = [];
  if (['.ts', '.tsx', '.mts', '.cts'].includes(request.suffix)) {
    plugins.push(['typescript', { dts: false, disallowAmbiguousJSXLike: ['.mts', '.cts'].includes(request.suffix) }]);
  }
  if (['.jsx', '.tsx'].includes(request.suffix)) plugins.push('jsx');
  let ast;
  try {
    ast = parse(request.text, {
      sourceType: request.mode, plugins, strictMode: true, errorRecovery: false,
      allowImportExportEverywhere: false, allowAwaitOutsideFunction: false,
      allowReturnOutsideFunction: false, allowSuperOutsideMethod: false,
      allowNewTargetOutsideFunction: false, allowYieldOutsideFunction: false,
      allowUndeclaredExports: false, attachComment: false,
    });
  } catch (error) {
    if (error instanceof SyntaxError) return { parsed: false, hits: [] };
    throw error;
  }
  if (ast.errors?.length || ast.type !== 'File' || ast.program?.type !== 'Program') {
    throw new Error('invalid syntax proof');
  }
  const wanted = new Set(request.tokens), hits = [];
  function add(id, kind) {
    if (id?.type !== 'Identifier' || !wanted.has(id.name)) return;
    // Escaped identifiers must not turn literal text into a different name.
    // TS Identifier.end can include its type annotation; the name is separate.
    const raw = request.text.slice(id.start, id.start + id.name.length);
    if (raw !== id.name || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(raw)) return;
    const start = Buffer.byteLength(request.text.slice(0, id.start), 'utf8');
    hits.push({ name: raw, line: id.loc.start.line, start,
      end: start + Buffer.byteLength(raw, 'utf8'), kind });
  }
  for (const statement of ast.program.body) {
    let node = statement;
    if (node.type === 'ExportNamedDeclaration') {
      if (node.exportKind === 'type' || node.source != null || node.specifiers?.length || !node.declaration) continue;
      node = node.declaration;
    } else if (node.type === 'ExportDefaultDeclaration') {
      node = node.declaration;
      if (!node || !['FunctionDeclaration', 'ClassDeclaration'].includes(node.type)) continue;
    }
    if (node.declare === true) continue;
    if (node.type === 'FunctionDeclaration' && node.body?.type === 'BlockStatement') {
      add(node.id, node.type);
    } else if (node.type === 'ClassDeclaration' && node.abstract !== true && node.body?.type === 'ClassBody') {
      add(node.id, node.type);
    } else if (node.type === 'VariableDeclaration' && ['const', 'let', 'var'].includes(node.kind)) {
      for (const declaration of node.declarations) {
        if (declaration.type === 'VariableDeclarator' && declaration.init != null) add(declaration.id, node.type);
      }
    }
  }
  return { parsed: true, hits };
}

try {
  for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    const request = JSON.parse(line);
    const result = bindings(request);
    emit({ id: request.id, oid: request.oid, ...result });
  }
} catch {
  // Do not print code, filenames, environment values, or parser diagnostics.
  process.exitCode = 2;
}
