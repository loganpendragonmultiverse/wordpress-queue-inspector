"""Reproducible standard-library 100,000-action JSON/UI benchmark. Run at repo root."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

count = 100_000
snapshot = {
    'schema':'wqi.snapshot/1','site':'benchmark',
    'captured_start':'2026-11-01T12:00:00Z','captured_end':'2026-11-01T12:01:00Z',
    'source':{},'coverage':{'action':{'complete':True,'source_count':count},'cron':{'complete':True,'source_count':0},'log':{'complete':True,'source_count':0}},
    'jobs':[{'kind':'action','id':str(i),'hook':'batch','group':'','status':'pending','scheduled':'2026-11-01T12:00:00Z'} for i in range(count)],'logs':[]}
with tempfile.TemporaryDirectory() as root:
    source = Path(root)/'snapshot.json'
    source.write_text(json.dumps(snapshot), encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    started = time.monotonic()
    server = subprocess.Popen([sys.executable,'-m','wqi','serve',str(source),'--port',str(port)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        server.stdout.readline()
        print('json_bytes',source.stat().st_size,'startup_seconds',round(time.monotonic()-started,3))
        opener = build_opener(ProxyHandler({}))
        for route, expected in [('/?page=1000','>99999</td>'),('/?q=99999','1 matching jobs')]:
            started = time.monotonic()
            with opener.open(f'http://127.0.0.1:{port}{route}',timeout=30) as response:
                body = response.read().decode()
            if expected not in body: raise AssertionError(f'missing {expected}')
            print(route,'seconds',round(time.monotonic()-started,3))
        status = Path(f'/proc/{server.pid}/status')
        if status.exists(): print(next(line for line in status.read_text().splitlines() if line.startswith('VmRSS:')))
    finally:
        server.terminate();server.wait(timeout=5);server.stdout.close()
