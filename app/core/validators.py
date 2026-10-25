import asyncio
import ipaddress
import re
import socket
import urllib.parse

from fastapi import HTTPException, status


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent directory traversal and remove problematic characters.
    Replaces non-alphanumeric (except . - _) with underscores.
    """
    cleaned = re.sub(r"[^\w\s\.-]", "_", filename.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "unnamed_file"


async def validate_safe_url(url_str: str) -> str:
    """
    SSRF Prevention (EC-06.2):
    Ensure URL uses HTTP/HTTPS and does not resolve to private, loopback,
    link-local, multicast, or reserved internal IP addresses.
    Runs DNS address resolution via threadpool to prevent stalling the asyncio event loop.
    """
    parsed = urllib.parse.urlsplit(url_str.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL protocol. Only HTTP and HTTPS protocols are permitted.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid URL: Missing hostname.",
        )

    # Disallow known local/internal aliases directly
    lower_host = hostname.lower()
    if lower_host in (
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "metadata.google.internal",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="External links pointing to internal or private IP addresses are strictly forbidden.",
        )

    # Resolve hostname to all candidate IP addresses without blocking event loop
    try:
        addr_info = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Hostname '{hostname}' could not be resolved.",
        ) from exc

    for entry in addr_info:
        sockaddr = entry[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"External links pointing to internal or private IP addresses ({ip_str}) are strictly forbidden.",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid IP address encountered during URL validation.",
            ) from exc

    return url_str.strip()
