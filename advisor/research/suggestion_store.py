"""Durable single-writer suggestion releases; latest JSON is the commit point.

Rows are written before publication. Readers ignore rows from unreachable
(uncommitted) releases. A crash before latest replacement leaves last-good
intact; a retry can safely append the same immutable row IDs.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        with tmp.open('w') as f:
            json.dump(value, f, sort_keys=True, allow_nan=False, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def writer_lock(research):
    Path(research).mkdir(parents=True, exist_ok=True)
    with (Path(research) / '.suggestions.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def read_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    rows = []
    for i, line in enumerate(raw.splitlines(keepends=True)):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('journal row must be an object')
            rows.append(row)
        except (json.JSONDecodeError, UnicodeDecodeError):
            if not line.endswith(b'\n') and i == len(raw.splitlines()) - 1:
                break  # crash tail; repaired before next append under the lock
            raise ValueError(f'Corrupt suggestion journal row {i + 1}')
    return rows


def append_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Caller owns writer_lock. Never append behind a partial crash tail.
    existing = read_rows(path)
    if path.exists():
        raw = path.read_bytes()
        if raw and not raw.endswith(b'\n'):
            try:
                json.loads(raw.splitlines()[-1])
                with path.open('ab') as f:
                    f.write(b'\n'); f.flush(); os.fsync(f.fileno())
            except (ValueError, UnicodeDecodeError):
                with path.open('r+b') as f:
                    f.truncate(raw.rfind(b'\n') + 1); f.flush(); os.fsync(f.fileno())
    known = {digest(r) for r in existing}
    with path.open('a') as f:
        for row in rows:
            if digest(row) not in known:
                f.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
                known.add(digest(row))
        f.flush(); os.fsync(f.fileno())


def committed_ids(research):
    research = Path(research)
    latest = research / 'picks_latest.json'
    if not latest.exists():
        return set()
    doc = json.loads(latest.read_text())
    result = set()
    release = doc.get('release_id')
    if release and digest({k: v for k, v in doc.items() if k != 'release_id'}) != release:
        raise ValueError('Latest suggestion digest mismatch')
    while release:
        if release in result or len(release) != 64 or any(c not in '0123456789abcdef' for c in release):
            raise ValueError('Invalid suggestion release chain')
        result.add(release)
        archive = json.loads((research / 'suggestion_releases' / f'{release}.json').read_text())
        if digest({k: v for k, v in archive.items() if k != 'release_id'}) != release:
            raise ValueError('Suggestion release digest mismatch')
        release = archive.get('previous_release_id')
    return result


def commit(research, out, ledger, result, rows):
    research = Path(research)
    previous = json.loads(Path(out).read_text()) if Path(out).exists() else {}
    if previous.get('release_id'):
        committed_ids(research)  # refuse to extend a corrupt chain
    result['previous_release_id'] = previous.get('release_id')
    # Row payload is part of the immutable release. The archive can repair a
    # missing legacy journal mirror without inventing any issue-time facts.
    result['issue_rows'] = rows
    release = digest(result)
    result['release_id'] = release
    atomic_json(research / 'suggestion_releases' / f'{release}.json', result)
    append_rows(ledger, [{**r, 'release_id': release} for r in rows])
    atomic_json(out, result)  # only commit point
    return result
