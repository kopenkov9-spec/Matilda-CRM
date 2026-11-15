"""TLS entry point forwarding to the one local CRM worker. No public inference endpoint."""
import json
import http.client
import ssl
import threading
import time
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from .tls_setup import ensure_certificate
from .network import local_addresses

ROOT=Path(__file__).resolve().parent.parent

def main():
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    class Handler(BaseHTTPRequestHandler):
        timeout = 15
        def forward(self):
            # Local owner login is never reachable through the LAN proxy.
            route=unquote(self.path.split('?')[0]).rstrip('/')
            if route=='/api/auth/local' or route.startswith('/api/lifecycle'):
                self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<0 or size>13*1024*1024 or self.headers.get('Transfer-Encoding'):
                    self.send_error(413);return
                incoming_host=self.headers.get('Host','')
                if incoming_host not in {f'{address}:8443' for address in local_addresses()}:
                    self.send_error(403);return
                origin=self.headers.get('Origin')
                if origin and origin!='https://'+incoming_host:
                    self.send_error(403);return
                headers={k:v for k,v in self.headers.items() if k.lower() not in ('connection','host','origin','x-forwarded-for','x-forwarded-proto','forwarded')}
                headers['Host']=f'127.0.0.1:{config["crm_port"]}'
                if origin:headers['Origin']='http://'+headers['Host']
                conn=http.client.HTTPConnection('127.0.0.1',config['crm_port'],timeout=30)
                try:
                    conn.request(self.command,self.path,self.rfile.read(size),headers)
                    response=conn.getresponse();data=response.read()
                    self.send_response(response.status)
                    for k,v in response.getheaders():
                        if k.lower() in ('transfer-encoding','connection','content-length'):continue
                        if k.lower()=='set-cookie' and 'secure' not in v.lower():v+='; Secure'
                        self.send_header(k,v)
                    self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
                finally:
                    conn.close()
            except Exception:
                self.send_error(502)
        do_GET=do_POST=do_PUT=do_PATCH=do_DELETE=forward
        def log_message(self,format,*args):pass
    server=ThreadingHTTPServer(('0.0.0.0',8443),Handler)
    def context_for_current_network():
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(ROOT/'data/tls/server.crt',ROOT/'data/tls/server.key')
        return context
    ensure_certificate()
    context=context_for_current_network()
    server.socket=context.wrap_socket(server.socket,server_side=True)
    def watch_network():
        while True:
            time.sleep(15)
            try:
                if ensure_certificate():
                    server.socket.context=context_for_current_network()
            except (OSError,RuntimeError):
                pass  # Temporarily disconnected; retry when a network returns.
    threading.Thread(target=watch_network,daemon=True).start()
    server.serve_forever()

if __name__=='__main__':main()
