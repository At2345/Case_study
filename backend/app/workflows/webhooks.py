"""Outbound webhook calls from workflow nodes (e.g. into n8n), with SSRF protection.

- Only http(s); the host is resolved and every address must be public unless
  WORKFLOW_ALLOW_PRIVATE_WEBHOOKS=true (needed for a self-hosted n8n on localhost or a LAN).
- No redirects are followed, the response is capped, and a timeout applies.
- Each call carries an Idempotency-Key ({run_id}:{node_id}) so the receiver can ignore retries.
- Secrets never live in workflow JSON: secret_ref names an environment variable WF_SECRET_<REF>.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 64 * 1024
Resolver = Callable[[str, int], List[str]]


class WebhookError(RuntimeError):
    pass


def _resolve(host: str, port: int) -> List[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})


def check_url(url: str, resolver: Resolver = _resolve, allow_private: Optional[bool] = None) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise WebhookError("webhook URL must be http(s) with a host")
    if parts.username or parts.password:
        raise WebhookError("webhook URL must not contain credentials")
    if allow_private is None:
        allow_private = os.environ.get("WORKFLOW_ALLOW_PRIVATE_WEBHOOKS", "false").lower() == "true"
    if allow_private:
        return
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addresses = resolver(parts.hostname, port)
    except OSError as exc:
        raise WebhookError(f"cannot resolve {parts.hostname}: {exc}")
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not ip.is_global:
            raise WebhookError(f"{parts.hostname} resolves to a non-public address ({ip}); "
                               "set WORKFLOW_ALLOW_PRIVATE_WEBHOOKS=true for a self-hosted n8n")


def post_json(url: str, body: Dict[str, Any], idempotency_key: str, secret_ref: Optional[str] = None,
              timeout_s: float = 10.0, transport: Optional[httpx.BaseTransport] = None,
              resolver: Resolver = _resolve) -> Dict[str, Any]:
    check_url(url, resolver)
    headers = {"Content-Type": "application/json", "Idempotency-Key": idempotency_key,
               "User-Agent": "sop-workbench-workflows/1"}
    if secret_ref:
        secret = os.environ.get(f"WF_SECRET_{secret_ref}")
        if not secret:
            raise WebhookError(f"secret WF_SECRET_{secret_ref} is not configured on the server")
        headers["X-Workflow-Secret"] = secret
    raw, truncated = b"", False
    with httpx.Client(timeout=timeout_s, follow_redirects=False, transport=transport) as client:
        with client.stream("POST", url, content=json.dumps(body, default=str), headers=headers) as resp:
            status = resp.status_code
            for chunk in resp.iter_bytes():
                raw += chunk
                if len(raw) > MAX_RESPONSE_BYTES:
                    raw, truncated = raw[:MAX_RESPONSE_BYTES], True
                    break
    if status >= 400:
        raise WebhookError(f"webhook returned HTTP {status}: {raw[:300].decode('utf-8', 'replace')}")
    if 300 <= status < 400:
        raise WebhookError(f"webhook redirected (HTTP {status}); redirects are not followed")
    data: Any = None
    if raw and not truncated:
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw.decode("utf-8", "replace")
    elif raw:
        data = raw.decode("utf-8", "replace")
    return {"status_code": status, "response": data, "truncated": truncated}
