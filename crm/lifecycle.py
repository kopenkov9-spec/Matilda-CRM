"""A live connection per desktop tab; no timer-based browser heartbeat expiry."""
import asyncio
import os
from pathlib import Path
import time
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse


class DesktopLifecycle:
    def __init__(self):
        self.stop_path = os.environ.get('MATILDA_STOP_FILE')
        self.clients = set()
        self.last_left = None

    def shutdown(self):
        if self.stop_path:
            Path(self.stop_path).touch()

    async def watch(self):
        while True:
            await asyncio.sleep(1)
            if self.last_left is not None and not self.clients and time.monotonic()-self.last_left >= 15:
                self.shutdown()
                return

    def attach(self, app, require_admin):
        def check(request):
            require_admin(request)
            if request.client.host not in ('127.0.0.1','::1','testclient'):
                raise HTTPException(403, 'Управлението е достъпно само от основния компютър.')

        @app.get('/api/lifecycle/status')
        def status(request: Request):
            check(request)
            return {'managed': bool(self.stop_path), 'close_delay_seconds': 15}

        @app.get('/api/lifecycle/desktop')
        async def desktop(request: Request):
            check(request)
            if not self.stop_path:
                raise HTTPException(409, 'Стартирайте Матилда от прекия път.')
            async def events():
                client = object()
                self.clients.add(client)
                self.last_left = None
                try:
                    while not await request.is_disconnected():
                        yield 'data: connected\n\n'
                        await asyncio.sleep(2)
                finally:
                    self.clients.discard(client)
                    if not self.clients:
                        self.last_left = time.monotonic()
            return StreamingResponse(events(), media_type='text/event-stream', headers={'X-Accel-Buffering':'no'})

        @app.post('/api/lifecycle/shutdown')
        def shutdown(body: dict, request: Request):
            check(request)
            if not self.stop_path:
                raise HTTPException(409, 'За изключване от интерфейса стартирайте Матилда от прекия път.')
            self.shutdown()
            return {'ok': True}
