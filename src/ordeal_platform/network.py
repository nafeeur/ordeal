"""Restricted outbound HTTP. DNS is validated once and the connection is pinned to that IP.

Application allowlists are not a substitute for firewall/network policy. Redirects are never followed.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit

from .config import Settings


class NetworkDenied(ValueError):
    pass


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, ip, port, timeout):
        super().__init__(hostname, port, timeout=timeout, context=ssl.create_default_context())
        self.pinned_ip = ip

    def connect(self):
        raw = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def validate_url(url: str, settings: Settings) -> tuple:
    u = urlsplit(url)
    if u.scheme not in {"https", "http"} or not u.hostname or u.username or u.password or u.fragment:
        raise NetworkDenied("Only explicit HTTP(S) URLs without userinfo or fragments are supported")
    host = u.hostname.lower()
    if host not in settings.outbound_hosts:
        raise NetworkDenied("Destination is not in ORDEAL_OUTBOUND_HOSTS")
    if settings.production and u.scheme != "https":
        raise NetworkDenied("Production outbound requests require HTTPS")
    port = u.port or (443 if u.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise NetworkDenied("Invalid destination port")
    if not settings.allow_private_outbound and port != 443:
        raise NetworkDenied("Only outbound port 443 is permitted outside development")
    return u, host, port


def request(settings: Settings, method: str, url: str, *, body: bytes = b"", headers: dict | None = None,
            timeout: float = 15, max_bytes: int = 2 * 1024 * 1024) -> tuple[int, dict, bytes]:
    u, host, port = validate_url(url, settings)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(entry[4][0] for entry in addresses))
    if not ips:
        raise NetworkDenied("Destination did not resolve")
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        # Metadata/link-local/multicast/unspecified are always blocked, including development.
        if addr.is_link_local or addr.is_multicast or addr.is_unspecified:
            raise NetworkDenied("Unsafe destination address")
        if not settings.allow_private_outbound and not addr.is_global:
            raise NetworkDenied("Non-public destination address")
    ip = ips[0]
    conn = PinnedHTTPSConnection(host, ip, port, timeout) if u.scheme == "https" else http.client.HTTPConnection(ip, port, timeout=timeout)
    clean_headers = dict(headers or {})
    for key, val in clean_headers.items():
        if "\r" in str(key) + str(val) or "\n" in str(key) + str(val):
            raise NetworkDenied("Invalid HTTP header")
    host_header = f"[{host}]" if ":" in host else host
    if port not in {80, 443}:
        host_header += f":{port}"
    clean_headers["Host"] = host_header
    clean_headers["Connection"] = "close"
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    try:
        conn.request(method, path, body=body or None, headers=clean_headers)
        res = conn.getresponse()
        data = res.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise NetworkDenied("Outbound response exceeded size limit")
        if 300 <= res.status < 400:
            raise NetworkDenied("Outbound redirects are prohibited")
        return res.status, dict(res.getheaders()), data
    finally:
        conn.close()


def json_request(settings, method, url, data=None, headers=None):
    status, response_headers, body = request(settings, method, url,
        body=json.dumps(data, allow_nan=False).encode() if data is not None else b"",
        headers={"Content-Type": "application/json", **(headers or {})})
    if not 200 <= status < 300:
        raise RuntimeError(f"Remote endpoint returned HTTP {status}")
    return json.loads(body) if body else {}
