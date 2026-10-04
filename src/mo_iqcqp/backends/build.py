"""Reject stale native builds instead of silently running a different engine."""
import hashlib
import json
from pathlib import Path

def sha256(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def source_hash(root):
    root=Path(root)
    files=[root/'native/worker.cpp',root/'scripts/build_native.py',Path(__file__)]
    files+=sorted(p for p in (root/'third_party/ls-iqcqp').iterdir() if p.suffix in ('.cpp','.h'))
    digest=hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()

def verify(root,executable):
    executable=Path(executable)
    try:manifest=json.loads(executable.with_suffix('.manifest.json').read_text())
    except (OSError,ValueError) as exc:raise ValueError('Native build manifest missing; run scripts/build_native.py') from exc
    actual=sha256(executable)
    if manifest.get('source_sha256')!=source_hash(root) or manifest.get('binary_sha256')!=actual:
        raise ValueError('Native build is stale or modified; run scripts/build_native.py')
    return actual
