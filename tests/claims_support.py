"""Entirely synthetic transcripts; no recorded user sessions or private paths."""
from __future__ import annotations

import json
from pathlib import Path

from smtw.claims.model import line_counts


def agy_call(name, **args):
    return {'type': 'PLANNER_RESPONSE', 'source': 'MODEL', 'status': 'DONE',
            'created_at': '2026-01-01T00:00:00Z',
            'tool_calls': [{'name': name, 'args': args}]}


def generic(content, status='DONE'):
    return {'type': 'GENERIC', 'source': 'MODEL', 'status': status, 'content': content}


def view(path, text, start=1, end=None):
    lines = text.split('\n') if text else []
    end = len(lines) if end is None else end
    header = (f'File Path: `file:///{path}`\nTotal Lines: {line_counts(text)["L_view"]}\n'
              f'Total Bytes: {len(text.encode("utf-8"))}\nShowing lines {start} to {end}\n')
    return generic(header + '\n'.join(f'{n}: {lines[n-1]}' for n in range(start, min(len(lines), end)+1)))


def write_transcript(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')
    return path


def make_case(tmp_path, text, rows=()):
    report = tmp_path / 'report.md'
    report.write_text(text, encoding='utf-8')
    transcript = write_transcript(tmp_path / 'transcript_slice.jsonl', rows)
    return report, transcript
