"""Graceful app stop requested by its owning launcher."""
import json
import os
from pathlib import Path
import threading
import time
import uvicorn

root = Path(__file__).resolve().parent.parent
config = json.loads((root/'config.json').read_text(encoding='utf-8'))
server = uvicorn.Server(uvicorn.Config('crm.app:create_app', factory=True, host='127.0.0.1',
                                     port=config['crm_port'], proxy_headers=False, timeout_graceful_shutdown=5))

def watch():
    stop = Path(os.environ['MATILDA_STOP_FILE'])
    while not server.should_exit:
        if stop.exists():
            server.should_exit = True
            return
        time.sleep(0.5)

threading.Thread(target=watch, daemon=True).start()
server.run()
