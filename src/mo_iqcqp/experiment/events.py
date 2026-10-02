"""Synchronous JSONL event stream with bounded in-memory tail and integrity marker."""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import tempfile


class EventStream:
    def __init__(self, output=None, recent_capacity=64):
        if output is None:
            root=Path(__file__).resolve().parents[3]/'data/cache/events'
            root.mkdir(parents=True,exist_ok=True)
            fd,path=tempfile.mkstemp(prefix='run-',suffix='.jsonl',dir=root)
            self.path=Path(path).resolve()
        else:
            self.path=Path(str(output)+'.events.jsonl').resolve()
            self.path.parent.mkdir(parents=True,exist_ok=True)
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        self.fd=fd;self.count=0;self.bytes=0;self.hash=hashlib.sha256()
        self.recent=deque(maxlen=recent_capacity);self.complete=False

    def _write(self, payload):
        data=(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str,allow_nan=False)+'\n').encode()
        offset=0
        while offset<len(data):
            written=os.write(self.fd,data[offset:])
            if written<=0:raise OSError('Event log write returned zero bytes')
            offset+=written
        return data

    def append(self,event):
        if self.fd is None:raise RuntimeError('Event log closed')
        data=self._write(dict(type='event',data=event))
        self.hash.update(data);self.bytes+=len(data);self.count+=1;self.recent.append(event)

    def flush(self):
        if self.fd is not None:os.fsync(self.fd)

    def close(self,complete=True):
        if self.fd is None:return
        try:
            if complete:
                self._write(dict(type='end',records=self.count,bytes=self.bytes,sha256=self.hash.hexdigest()))
                os.fsync(self.fd);self.complete=True
            else:os.fsync(self.fd)
        finally:
            os.close(self.fd);self.fd=None

    def descriptor(self):
        return dict(path=str(self.path),records=self.count,bytes=self.bytes,
                    sha256=self.hash.hexdigest(),complete=self.complete,
                    recent_capacity=self.recent.maxlen)


def inspect_event_log(path):
    """Bounded-memory recovery/verification; truncated final line remains visible."""
    path=Path(path)
    if not path.exists():return None
    digest=hashlib.sha256();count=0;size=0;end=None;tail_complete=True
    with path.open('rb') as f:
        for line in f:
            if not line.endswith(b'\n'):
                tail_complete=False;break
            try:entry=json.loads(line)
            except (ValueError,UnicodeDecodeError):
                tail_complete=False;break
            if not isinstance(entry,dict):tail_complete=False;break
            if entry.get('type')=='end':
                end=entry
                if f.read(1):tail_complete=False
                break
            if entry.get('type')!='event':tail_complete=False;break
            digest.update(line);size+=len(line);count+=1
    complete=bool(tail_complete and end and end.get('records')==count
                  and end.get('bytes')==size and end.get('sha256')==digest.hexdigest())
    return dict(path=str(path),records=count,bytes=size,sha256=digest.hexdigest(),
                complete=complete,tail_complete=tail_complete,recent_capacity=64)


def read_events(path):
    """Stream current JSONL without accumulating events in memory."""
    with Path(path).open(encoding='utf-8') as source:
        for line in source:
            record=json.loads(line)
            if record.get('type')=='event':yield record['data']


def events_from_result(result):
    """Read current event sidecar, or historical embedded events without rewriting it."""
    if result.get('events_log'):
        yield from read_events(result['events_log']['path'])
    else:
        yield from result.get('events',())
