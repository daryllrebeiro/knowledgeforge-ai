"""SSRF (Server-Side Request Forgery) protection for outbound webhook delivery."""

import ipaddress
import socket
from urllib.parse import urlparse


class SSRFValidationError(ValueError):
    """Raised when a webhook URL targets private, loopback, or metadata addresses."""

    pass


BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local & cloud metadata (169.254.169.254)
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def is_ip_blocked(ip_addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address falls into any blocked or private CIDR range."""
    return any(ip_addr in net for net in BLOCKED_NETWORKS)


def validate_webhook_url(url: str, *, allow_private: bool = False) -> str:
    """Validate that a webhook target URL is safe from SSRF.

    Enforces:
    1. Scheme must be http or https.
    2. Hostname must resolve to an IP address.
    3. Resolved IP must NOT be in loopback, RFC 1918 private, link-local, or cloud metadata ranges.

    Raises:
        SSRFValidationError: if the URL targets a forbidden destination.
    """
    if not url or not isinstance(url, str):
        raise SSRFValidationError("Webhook URL must be a non-empty string")

    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise SSRFValidationError(
            f"Invalid URL scheme '{parsed.scheme}'; only http and https are permitted"
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("Webhook URL is missing a valid hostname")

    # Reject obvious metadata targets unconditionally
    lower_host = hostname.lower()
    if lower_host in ("metadata.google.internal", "169.254.169.254"):
        raise SSRFValidationError(f"SSRF violation: direct access to '{hostname}' is prohibited")

    if lower_host in ("localhost", "127.0.0.1", "::1") and not allow_private:
        raise SSRFValidationError(f"SSRF violation: direct access to '{hostname}' is prohibited")

    try:
        # Check if hostname is already an IP literal
        ip = ipaddress.ip_address(hostname)
        if ip == ipaddress.ip_address("169.254.169.254"):
            raise SSRFValidationError(
                f"SSRF violation: cloud metadata IP '{ip}' is strictly prohibited"
            )
        if not allow_private and is_ip_blocked(ip):
            raise SSRFValidationError(
                f"SSRF violation: IP '{ip}' is in a reserved or private address space"
            )
        return url
    except ValueError:
        # Hostname is a domain name; resolve via DNS
        pass

    try:
        addr_info = socket.getaddrinfo(
            hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except socket.gaierror as exc:
        raise SSRFValidationError(f"Cannot resolve hostname '{hostname}': {exc}") from exc

    resolved_ips = set()
    for item in addr_info:
        sockaddr = item[4]
        ip_str = sockaddr[0]
        try:
            resolved_ips.add(ipaddress.ip_address(ip_str))
        except ValueError:
            continue

    if not resolved_ips:
        raise SSRFValidationError(f"Could not resolve any valid IP addresses for '{hostname}'")

    if not allow_private:
        for ip in resolved_ips:
            if is_ip_blocked(ip):
                raise SSRFValidationError(
                    f"SSRF violation: hostname '{hostname}' resolves to private/blocked IP '{ip}'"
                )

    return url
