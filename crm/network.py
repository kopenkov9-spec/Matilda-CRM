"""Discover this machine's current private IPv4 addresses, never a packaged IP."""
import ipaddress
import socket

def local_addresses():
    candidates = []
    try:
        # UDP connect selects a route locally; no datagram is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(('192.0.2.1', 9))
            candidates.append(route.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    result = []
    for candidate in candidates:
        address = ipaddress.ip_address(candidate)
        if address.version == 4 and address.is_private and not (address.is_loopback or address.is_link_local or address.is_unspecified):
            if candidate not in result:
                result.append(candidate)
    return result

def phone_status(folder):
    addresses = local_addresses()
    urls = [f'https://{address}:8443' for address in addresses]
    configured = (folder/'server.crt').exists()
    with socket.socket() as sock:
        sock.settimeout(0.2)
        listening = sock.connect_ex(('127.0.0.1',8443)) == 0
    return {'phone_url':urls[0] if urls and configured else None,
            'phone_urls':urls if configured else [],
            'phone_status':'ready' if configured and listening and urls else 'offline' if configured else 'unconfigured'}
