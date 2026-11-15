"""Hidden supervisor; a private Windows Job Object owns all CRM processes."""
import argparse
import html
import json
import msvcrt
import os
from pathlib import Path
import socket
import subprocess
import time
import traceback
import uuid
import webbrowser
import urllib.request
from crm.process_group import ProcessGroup
ROOT = Path(__file__).resolve().parent

def healthy(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False

def occupied(port):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', port)) == 0

def open_browser(config):
    key = (ROOT/'data/owner-key.txt').read_text(encoding='utf-8').strip()
    webbrowser.open(f'http://127.0.0.1:{config["crm_port"]}/#setup={key}')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--https', action='store_true')
    args = parser.parse_args()
    runtime = ROOT/'runtime'
    runtime.mkdir(exist_ok=True)
    config = json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    url = f'http://127.0.0.1:{config["crm_port"]}/api/health'
    lock = (runtime/'launcher.lock').open('a+b')
    if lock.tell() == 0:
        lock.write(b'0'); lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if args.https:
            (runtime/'enable-https.flag').touch()
        for _ in range(60):
            if healthy(url):
                if not args.no_browser: open_browser(config)
                return
            time.sleep(0.5)
        raise RuntimeError('Matilda is still starting. See runtime/crm.stderr.log.')
    stop_file = runtime/'shutdown.flag'
    https_flag = runtime/'enable-https.flag'
    stop_file.unlink(missing_ok=True)
    https_flag.unlink(missing_ok=True)
    for port in (config['crm_port'], config['llm_port']):
        if occupied(port):
            raise RuntimeError(f'Port {port} is occupied. Close the previous CRM instance first.')
    name = 'Local\\Matilda-' + uuid.uuid4().hex
    group = ProcessGroup(name)
    children = []
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', MATILDA_JOB=name,
               MATILDA_STOP_FILE=str(stop_file), CRM_LLM_URL=f'http://127.0.0.1:{config["llm_port"]}')
    def start(exe, module, label):
        proc = subprocess.Popen([str(exe), '-B', str(ROOT/'crm/managed_process.py'), label, module],
                                cwd=ROOT, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        children.append(proc)
        group.assign(proc)
        (runtime/f'{label}.pid').write_text(str(proc.pid))
        return proc
    try:
        start(config['model_python'], 'crm.model_server', 'phi')
        app = start(ROOT/'.venv/Scripts/python.exe', 'crm.server', 'crm')
        for _ in range(60):
            if healthy(url): break
            if app.poll() is not None: raise RuntimeError('CRM did not start. See runtime/crm.stderr.log.')
            time.sleep(0.5)
        else:
            raise RuntimeError('CRM startup timed out. See runtime/crm.stderr.log.')
        tls = None
        def enable_https():
            if occupied(8443): raise RuntimeError('Port 8443 is already occupied.')
            return start(ROOT/'.venv/Scripts/python.exe', 'crm.tls_proxy', 'tls')
        if args.https: tls = enable_https()
        if not args.no_browser: open_browser(config)
        while not stop_file.exists() and app.poll() is None:
            if https_flag.exists():
                https_flag.unlink(missing_ok=True)
                if tls is None or tls.poll() is not None: tls = enable_https()
            time.sleep(0.5)
        stop_file.touch()
        try: app.wait(timeout=10)
        except subprocess.TimeoutExpired: pass
    finally:
        group.close()
        for child in children:
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: child.kill()
        stop_file.unlink(missing_ok=True)
        lock.close()

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        (ROOT/'runtime').mkdir(exist_ok=True)
        with (ROOT/'runtime/launcher.log').open('a', encoding='utf-8') as log:
            traceback.print_exc(file=log)
        page = ROOT/'runtime/startup-error.html'
        page.write_text('<!doctype html><meta charset="utf-8"><title>Matilda</title><h1>Matilda could not start</h1><p>' + html.escape(str(error)) + '</p><p>Details: runtime/launcher.log</p>', encoding='utf-8')
        webbrowser.open(page.as_uri())
