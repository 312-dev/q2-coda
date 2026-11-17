"""What the orchestration tools share: the trunk's name, where the queue keeps its state, the event log.

The trunk is the one integration branch: the queue (tools/ci/merge_queue.py) lands tested commits on it and
nothing else may move it (tools/ci/hooks/reference-transaction). Its name is $Q2_TRUNK, else the first
line of ~/.config/q2/trunk, else DEFAULT_TRUNK. docs/orchestration.md.
"""
import fcntl
import json
import os
import pathlib
import time

DEFAULT_TRUNK = 'release/p31'


def config_dir():
    return pathlib.Path(os.environ.get('Q2_CONFIG', pathlib.Path.home()/'.config/q2'))


def queue_dir():
    """The queue's state: events.log, status.json, the STOP file, flakes.jsonl, the workers' registry."""
    return pathlib.Path(os.environ.get('Q2_QUEUE_DIR', pathlib.Path.home()/'.cache/q2-queue'))


def trunk():
    if os.environ.get('Q2_TRUNK'):
        return os.environ['Q2_TRUNK']
    f = config_dir()/'trunk'
    if f.exists() and f.read_text().strip():
        return f.read_text().split()[0]
    return DEFAULT_TRUNK


def event(kind, text='', directory=None, **fields):
    """Append one line to events.log: `TIME KIND text key=value ...`. One writer at a time; a reader
    (tail -F, a Monitor) sees whole lines."""
    d = directory or queue_dir()
    d.mkdir(parents=True, exist_ok=True)
    extra = ' '.join(f'{k}={v}' for k, v in fields.items() if v not in (None, ''))
    line = ' '.join(x for x in (time.strftime('%Y-%m-%dT%H:%M:%S'), kind, text, extra) if x)
    with open(d/'events.log', 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(line.replace('\n', ' ') + '\n')
    return line


def write_json(path, data):
    """Replace path with data whole, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, indent=1) + '\n')
    tmp.replace(path)
