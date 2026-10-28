"""Optional JS/TS evidence, applied only after the legacy check is complete."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from queue import Empty, Queue
import re
import shutil
import subprocess
from threading import Thread
import time

from .paths import matches_path, normalize_path

VERSION = '8.0.8'
POLICY = 'module-bindings-v1'
ENTRY_SHA256 = '687860d40a126108176fcf0ff32f56c164c81fb045fffb7e2b246117fd6fa3a9'
INTEGRITY = 'sha512-Gq65JogAr0S2p0BzvfSJboyuiRnxDQApyOqHkKmkrvm+VlLK4gC3pKsiOKEdnhFBemrFbSnOu90mZZWDpiJnmQ=='
SUFFIXES = {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'}
WORKER = Path(__file__).with_suffix('.mjs')
TOKEN = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]{0,255}\Z')
MAX_TOKENS = 256
MAX_TOKEN_FILES = 64
MAX_FILES = 256
MAX_TREE = 50_000
MAX_BLOB = 1024 * 1024
MAX_BYTES = 32 * 1024 * 1024
MAX_METADATA = 2 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_SEARCH = 8 * 1024 * 1024
MAX_HITS = 10_000
FILE_SECONDS = 1.0
TOTAL_SECONDS = 5.0


@dataclass(frozen=True)
class JSParserConfig:
    """Explicit trusted paths; no environment/PATH/global package discovery."""
    node: Path
    package: Path


class NoProof(Exception):
    pass


def remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise NoProof
    return left


class Child:
    """Bound pipes while reading; a Python deadline also interrupts sync parse."""
    def __init__(self, args, *, deadline, limit, env=None, cwd=None):
        remaining(deadline)
        self.process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.queue, self.buffer, self.threads = Queue(), b'', []
        self.failed = False
        self.deadline = deadline
        try:
            remaining(deadline)
        except NoProof:
            self.close()  # Process creation itself may consume the budget.
            raise

        def reader(pipe, output):
            count = 0
            try:
                while chunk := pipe.read1(8192):
                    count += len(chunk)
                    if count > (limit if output else 65536):
                        self.failed = True
                        self.queue.put(NoProof())
                        break
                    if output:
                        self.queue.put(chunk)
            except (OSError, ValueError):
                self.failed = True
                self.queue.put(NoProof())
            finally:
                if output:
                    self.queue.put(None)
        for pipe, output in ((self.process.stdout, True), (self.process.stderr, False)):
            thread = Thread(target=reader, args=(pipe, output), daemon=True)
            thread.start()
            self.threads.append(thread)

    def send(self, data, *, close=False):
        def writer():
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
                if close:
                    self.process.stdin.close()
            except (OSError, ValueError):
                self.failed = True
                self.queue.put(NoProof())
        thread = Thread(target=writer, daemon=True)
        thread.start()
        self.threads.append(thread)

    def receive(self, deadline):
        if self.failed:
            raise NoProof
        try:
            item = self.queue.get(timeout=remaining(min(deadline, self.deadline)))
        except Empty:
            raise NoProof from None
        remaining(min(deadline, self.deadline))
        if isinstance(item, Exception):
            raise NoProof
        return item

    def line(self, deadline):
        remaining(min(deadline, self.deadline))
        while b'\n' not in self.buffer:
            chunk = self.receive(deadline)
            if chunk is None:
                raise NoProof
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        value = json.loads(line.decode('utf-8'))
        remaining(min(deadline, self.deadline))
        return value

    def all(self):
        chunks = []
        while (chunk := self.receive(self.deadline)) is not None:
            chunks.append(chunk)
        code = self.process.wait(timeout=remaining(self.deadline))
        if self.failed:
            raise NoProof
        return code, b''.join(chunks)

    def close(self):
        # Windows termination can race process exit or be delayed by host load.
        # Always wait after kill, and allow one bounded retry before giving up.
        for attempt in range(2):
            if self.process.poll() is None:
                try:
                    self.process.kill()
                except OSError:
                    pass  # The wait below decides whether the child exited.
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if attempt:
                    raise
            else:
                break
        for thread in self.threads:
            thread.join(timeout=0.05)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe and not pipe.closed:
                pipe.close()


def child_environment():
    return {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}


def git_executable():
    found = shutil.which('git')
    if not found:
        raise NoProof
    path = Path(found).resolve()
    if os.name == 'nt':
        # Git for Windows cmd/bin launchers spawn another git.exe. Killing only
        # that launcher leaves its child's pipes alive past the deadline.
        if path.parent.name.lower() in {'cmd', 'bin'}:
            for architecture in ('mingw64', 'mingw32'):
                direct = path.parent.parent / architecture / 'bin/git.exe'
                if direct.is_file():
                    return str(direct)
        if path.parent.parent.name.lower() not in {'mingw64', 'mingw32'}:
            raise NoProof  # Unknown wrapper layout: keep the original result.
    return str(path)


def git(root, args, deadline, *, data=b'', limit=None, codes=(0,)):
    child = Child([git_executable(), '-C', str(root), *args], deadline=deadline,
                  limit=MAX_SEARCH if limit is None else limit,
                  env=child_environment())
    try:
        child.send(data, close=True)
        code, output = child.all()
        if code not in codes:
            raise NoProof
        return output
    finally:
        child.close()


def supported(path):
    lower = path.lower()
    return (not lower.endswith(('.d.ts', '.d.mts', '.d.cts'))
            and PurePosixPath(lower).suffix in SUFFIXES
            and not {'docs', 'reviews'}.intersection(PurePosixPath(lower).parts[:-1]))


def configured(config, roots):
    node, package = Path(config.node), Path(config.package)
    if not node.is_absolute() or not package.is_absolute():
        raise NoProof
    node, package = node.resolve(), package.resolve()
    if node.name.lower() != ('node.exe' if os.name == 'nt' else 'node') or not node.is_file():
        raise NoProof
    if any(package.is_relative_to(root) or node.is_relative_to(root) for root in roots):
        raise NoProof
    manifest = package / 'package.json'
    entry = package / 'lib/index.js'
    if manifest.stat().st_size > 65536 or entry.stat().st_size > 4 * MAX_BLOB:
        raise NoProof
    info = json.loads(manifest.read_text(encoding='utf-8'))
    if info.get('name') != '@babel/parser' or info.get('version') != VERSION:
        raise NoProof
    if hashlib.sha256(entry.read_bytes()).hexdigest() != ENTRY_SHA256:
        raise NoProof
    return node, entry


def tree(root, revision, deadline):
    fixed = git(root, ['rev-parse', '--verify', revision + '^{commit}'], deadline).decode('ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{40}', fixed):
        raise NoProof
    data = git(root, ['ls-tree', '-r', '-l', '-z', fixed], deadline)
    remaining(deadline)
    rows = data.split(b'\0')
    if len(rows) - 1 > MAX_TREE:
        raise NoProof
    files = {}
    for number, row in enumerate(filter(None, rows)):
        if number % 256 == 0:
            remaining(deadline)
        header, path = row.split(b'\t', 1)
        mode, kind, oid, size = header.split()
        if mode not in (b'100644', b'100755') or kind != b'blob':
            continue  # Never follow symlinks/submodules.
        files[path.decode('utf-8')] = (oid.decode('ascii'), int(size))
    return fixed, files, len(rows) - 1


def candidates(root, revision, files, tokens, deadline):
    # NUL separates path/line, newline terminates source content. Binary ignored.
    data = git(root, ['grep', '--threads=1', '-I', '-n', '-z', '-F', '-f', '-', revision, '--',
                      *('*' + suffix for suffix in sorted(SUFFIXES))], deadline,
               data=('\n'.join(tokens) + '\n').encode('ascii'), codes=(0, 1))
    found = {token: set() for token in tokens}
    offset = 0
    while offset < len(data):
        remaining(deadline)
        first = data.index(b'\0', offset)
        second = data.index(b'\0', first + 1)
        end = data.find(b'\n', second + 1)
        if end < 0:
            end = len(data)
        path = data[offset:first].decode('utf-8')
        if not path.startswith(revision + ':') or not data[first+1:second].isdigit():
            raise NoProof
        path = path[len(revision)+1:]
        if path in files and supported(path):
            content = data[second+1:end]
            for token, paths in found.items():
                remaining(deadline)
                if len(paths) <= MAX_TOKEN_FILES and token.encode('ascii') in content:
                    paths.add(path)
        offset = end + 1
    return found


def blobs(root, files, paths, deadline, limit):
    unique = {files[path][0]: files[path][1] for path in paths}
    if any(size > MAX_BLOB for size in unique.values()) or sum(unique.values()) > limit:
        raise NoProof
    if not unique:
        return {}
    data = git(root, ['cat-file', '--batch'], deadline,
               data=('\n'.join(unique) + '\n').encode('ascii'), limit=limit + len(unique) * 128)
    result, offset = {}, 0
    for oid, size in unique.items():
        remaining(deadline)
        end = data.index(b'\n', offset)
        if data[offset:end] != f'{oid} blob {size}'.encode('ascii'):
            raise NoProof
        start, offset = end + 1, end + size + 2
        content = data[start:start+size]
        if data[offset-1:offset] != b'\n':
            raise NoProof
        # Do not decode damaged files with replacement or a different encoding.
        try:
            result[oid] = content.decode('utf-8')
        except UnicodeDecodeError:
            result[oid] = None
    if offset != len(data):
        raise NoProof
    return result


def package_paths(path, files):
    for parent in PurePosixPath(path).parents:
        name = str(parent / 'package.json')
        if name in files:
            return name
    return None


def source_mode(path, files, content):
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {'.mjs', '.mts', '.ts', '.tsx'}:
        return 'module'
    if suffix in {'.cjs', '.cts'}:
        return 'script'
    name = package_paths(path, files)
    if name is None:
        return 'script'
    try:
        config = json.loads(content[files[name][0]])
        kind = config.get('type', 'commonjs')
    except (TypeError, ValueError, AttributeError):
        return None
    return {'module': 'module', 'commonjs': 'script'}.get(kind) if isinstance(kind, str) else None


def eligible(claim):
    return (claim.check_type == 'K4' and claim.verdict == 'absent'
            and claim.reason_code == 'definition_missing' and claim.path
            and TOKEN.fullmatch(claim.value) and not claim.when
            and not (claim.negated_context or claim.contextual_reason or claim.binding_candidates
                     or claim.malformed_number))


def prove(claims, repositories, config, deadline):
    node, entry = configured(config, repositories.roots)
    tokens = sorted({c.value for c in claims if eligible(c)})
    if not tokens or len(tokens) > MAX_TOKENS:
        return {}
    plans, total_tree, per_token = [], 0, {token: 0 for token in tokens}
    for root in repositories.roots:
        revision = repositories.commits.get(normalize_path(str(root)))
        if not revision:
            continue
        fixed, files, count = tree(root, revision, deadline)
        total_tree += count
        if total_tree > MAX_TREE:
            raise NoProof
        targets, by_basename = {}, {}
        for number, path in enumerate(files):
            if number % 256 == 0:
                remaining(deadline)
            by_basename.setdefault(path.rsplit('/', 1)[-1].casefold(), []).append(path)
        for index, c in enumerate(claims):
            if index % 256 == 0:
                remaining(deadline)
            if not eligible(c) or c.revision and not fixed.startswith(c.revision):
                continue
            basename = normalize_path(c.path, repositories.aliases).rsplit('/', 1)[-1]
            paths = set()
            for number, path in enumerate(by_basename.get(basename, [])):
                if number % 256 == 0:
                    remaining(deadline)
                if (matches_path(c.path, path, repositories.aliases)
                        or matches_path(c.path, str(root / path), repositories.aliases)):
                    paths.add(path)
            if paths:
                targets[index] = paths
        wanted = sorted({claims[index].value for index in targets})
        if not wanted:
            continue
        found = candidates(root, fixed, files, wanted, deadline)
        for token, paths in found.items():
            per_token[token] += len(paths)
        plans.append((root, fixed, files, targets, found))
    excluded = {token for token, count in per_token.items() if count > MAX_TOKEN_FILES}
    jobs, total_bytes, total_metadata, total_files = [], 0, 0, 0
    for root, fixed, files, targets, found in plans:
        paths = sorted(set().union(*(paths for token, paths in found.items() if token not in excluded)))
        total_files += len(paths)
        total_bytes += sum(files[path][1] for path in paths)
        metadata = {name for path in paths if PurePosixPath(path).suffix.lower() in {'.js', '.jsx'}
                    and (name := package_paths(path, files)) is not None}
        total_metadata += sum(files[path][1] for path in metadata)
        if total_files > MAX_FILES or total_bytes > MAX_BYTES or total_metadata > MAX_METADATA:
            raise NoProof
        content = blobs(root, files, set(paths) | metadata, deadline, MAX_BYTES + MAX_METADATA)
        for path in paths:
            remaining(deadline)
            text = content[files[path][0]]
            mode = source_mode(path, files, content)
            wanted = sorted(token for token, names in found.items() if token not in excluded and path in names)
            if text is not None and mode is not None:
                jobs.append((root, fixed, path, files[path][0], text, mode, wanted, targets))
    if not jobs:
        return {}
    # No PATH, NODE_OPTIONS, NODE_PATH, npm config or caller secrets in the child.
    environment = child_environment()
    worker = Child([str(node), '--max-old-space-size=128', str(WORKER), str(entry)],
                   deadline=deadline, limit=MAX_OUTPUT, env=environment, cwd=WORKER.parent)
    proofs, hit_count, proof_count, cache = {}, 0, 0, {}
    try:
        ready = worker.line(min(deadline, time.monotonic() + FILE_SECONDS))
        if ready != {'ready': True, 'version': VERSION, 'policy': POLICY}:
            raise NoProof
        for request_id, (root, fixed, path, oid, text, mode, wanted, targets) in enumerate(jobs):
            request = dict(id=request_id, oid=oid, text=text, mode=mode,
                           suffix=PurePosixPath(path).suffix.lower(), tokens=wanted)
            file_deadline = min(deadline, time.monotonic() + FILE_SECONDS)
            cache_key = (oid, mode, request['suffix'], tuple(wanted))
            if cache_key in cache:
                response = dict(cache[cache_key], id=request_id)
            else:
                worker.send((json.dumps(request, ensure_ascii=True) + '\n').encode('utf-8'))
                response = worker.line(file_deadline)
            if (set(response) != {'id', 'oid', 'parsed', 'hits'} or type(response['id']) is not int
                    or response['id'] != request_id
                    or response['oid'] != oid or type(response['parsed']) is not bool
                    or not isinstance(response['hits'], list) or not response['parsed'] and response['hits']):
                raise NoProof
            cache[cache_key] = response
            encoded = text.encode('utf-8')
            for hit in response['hits']:
                remaining(deadline)
                hit_count += 1
                if (hit_count > MAX_HITS or set(hit) != {'name', 'line', 'start', 'end', 'kind'}
                        or hit['name'] not in wanted or hit['kind'] not in {
                            'FunctionDeclaration', 'ClassDeclaration', 'VariableDeclaration'}
                        or any(type(hit[key]) is not int for key in ('line', 'start', 'end'))
                        or not 0 <= hit['start'] < hit['end'] <= len(encoded)
                        or encoded[hit['start']:hit['end']] != hit['name'].encode('ascii')
                        or hit['line'] != len(re.split(rb'\r\n|\r|\n|\xe2\x80[\xa8\xa9]', encoded[:hit['start']]))):
                    raise NoProof
                for index, linked in targets.items():
                    if index % 256 == 0:
                        remaining(deadline)
                    if claims[index].value == hit['name'] and path not in linked:
                        proof_count += 1
                        if proof_count > MAX_HITS:
                            raise NoProof
                        proofs.setdefault(index, []).append(dict(
                            path=path, line=hit['line'], revision=fixed, kind='definition',
                            blob=oid, parser='@babel/parser', parser_version=VERSION,
                            parser_sha256=ENTRY_SHA256, policy=POLICY, source_type=mode,
                            ast_kind=hit['kind']))
        worker.process.stdin.close()
        code, extra = worker.all()
        if code != 0 or extra or worker.buffer:
            raise NoProof
        remaining(deadline)
        return proofs
    finally:
        worker.close()


def rescue(claims, repositories, config):
    """All optional failures stay here, outside the legacy timeout handler.

    No original claim/cache is mutated until the whole proof request succeeds.
    """
    if config is None or not any(eligible(c) for c in claims):
        return
    try:
        seconds = min(TOTAL_SECONDS, repositories.budget.remaining() - 1.0)
        if seconds <= 0:
            return
        proofs = prove(claims, repositories, config, time.monotonic() + seconds)
    except Exception:
        return  # Optional syntax evidence is never required to keep an absence.
    for index, hits in proofs.items():
        c = claims[index]
        c.source = {**c.source, 'repository_hits': hits, 'repository_hit_kind': 'definition',
                    'repository_search_scope': ['fixed-tree JS/TS AST module bindings']}
        c.verdict, c.reason_code = 'unverifiable', 'found_outside_scope'
        c.reason = '고정 저장소의 다른 파일에서 JS/TS 최상위 AST 바인딩 확인'
