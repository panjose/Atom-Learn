"""Credential-bounded public/private GitHub Release asset transport."""

from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import MANAGER_VERSION
from .common import ManagerError


RELEASE_ASSET = re.compile(r"^/([^/]+/[^/]+)/releases/download/([^/]+)/([^/]+)$")
AUTH_HOSTS = {"github.com", "api.github.com"}


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: BinaryIO, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old_host = urlparse(req.full_url).hostname
        new_host = urlparse(newurl).hostname
        if old_host != new_host or new_host not in AUTH_HOSTS:
            redirected.remove_header("Authorization")
        return redirected


def _credential() -> tuple[str | None, str]:
    for name in ["ATOMLEARN_GITHUB_TOKEN", "GH_TOKEN"]:
        value = os.environ.get(name)
        if value:
            return value, f"environment:{name}"
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, "none"
    token = result.stdout.strip() if result.returncode == 0 else ""
    return (token, "github_cli") if token else (None, "none")


def _request(url: str, accept: str, token: str | None = None) -> Request:
    host = urlparse(url).hostname
    if token and host not in AUTH_HOSTS:
        raise ManagerError("Refusing to send GitHub credentials to a non-allowlisted host")
    headers = {"Accept": accept, "User-Agent": f"AtomLearnManager/{MANAGER_VERSION}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    return Request(url, headers=headers)


def _typed_http(url: str, status: int, provider: str) -> ManagerError:
    return ManagerError(
        f"GitHub Release asset request failed with HTTP {status}; the release may be private, unavailable, or inaccessible",
        code="release_asset_http_error",
        retryable=500 <= status < 600,
        details={"host": urlparse(url).hostname or "unknown", "status": status, "credential_provider": provider},
    )


def _private_asset_request(url: str, accept: str, token: str, provider: str) -> Request:
    parsed = urlparse(url)
    match = RELEASE_ASSET.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.query or parsed.fragment or not match:
        raise ManagerError("Authenticated transport is limited to immutable github.com Release asset URLs")
    repository, tag, filename = match.groups()
    api = f"https://api.github.com/repos/{repository}/releases/tags/{quote(tag, safe='')}"
    opener = build_opener(_SafeRedirect())
    try:
        with opener.open(_request(api, "application/vnd.github+json", token), timeout=20) as response:
            metadata = json.loads(response.read(2 * 1024 * 1024 + 1).decode("utf-8"))
    except HTTPError as exc:
        raise _typed_http(api, int(exc.code), provider) from exc
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError(
            "Private GitHub Release metadata is unavailable; the active Core is unchanged",
            code="release_asset_unavailable",
            retryable=True,
            details={"host": "api.github.com", "credential_provider": provider},
        ) from exc
    assets = metadata.get("assets", []) if isinstance(metadata, dict) else []
    candidates = [item for item in assets if isinstance(item, dict) and item.get("name") == filename]
    if len(candidates) != 1 or not isinstance(candidates[0].get("url"), str):
        raise ManagerError(
            f"Private GitHub Release does not contain exactly one asset named {filename}",
            code="release_asset_not_found",
            details={"host": "api.github.com", "credential_provider": provider},
        )
    return _request(candidates[0]["url"], "application/octet-stream", token)


def open_release_asset(url: str, accept: str):
    opener = build_opener(_SafeRedirect())
    try:
        response = opener.open(_request(url, accept), timeout=30)
        if not response.geturl().startswith("https://"):
            response.close()
            raise ManagerError("Release asset redirect must remain on HTTPS")
        return response, "public"
    except HTTPError as public_error:
        if int(public_error.code) not in {401, 403, 404}:
            raise _typed_http(url, int(public_error.code), "none") from public_error
        token, provider = _credential()
        if not token:
            raise _typed_http(url, int(public_error.code), provider) from public_error
        private_request = _private_asset_request(url, accept, token, provider)
        try:
            response = opener.open(private_request, timeout=30)
        except HTTPError as exc:
            raise _typed_http(url, int(exc.code), provider) from exc
        except (OSError, URLError) as exc:
            raise ManagerError(
                "Private GitHub Release asset is unavailable; the active Core is unchanged",
                code="release_asset_unavailable",
                retryable=True,
                details={"host": "api.github.com", "credential_provider": provider},
            ) from exc
        if not response.geturl().startswith("https://"):
            response.close()
            raise ManagerError("Private Release asset redirect must remain on HTTPS")
        return response, provider
    except (OSError, URLError) as exc:
        raise ManagerError(
            "GitHub Release asset is temporarily unavailable; the active Core is unchanged",
            code="release_asset_unavailable",
            retryable=True,
            details={"host": urlparse(url).hostname or "unknown", "credential_provider": "none"},
        ) from exc


def fetch_release_bytes(url: str, *, accept: str, limit: int) -> tuple[bytes, str]:
    response, provider = open_release_asset(url, accept)
    with closing(response):
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ManagerError("Release asset exceeds its bounded size limit")
    return content, provider


def download_release_asset(url: str, destination: Path, *, expected_size: int) -> str:
    response, provider = open_release_asset(url, "application/octet-stream")
    with closing(response), destination.open("xb") as writer:
        remaining = expected_size
        while remaining:
            block = response.read(min(1024 * 1024, remaining))
            if not block:
                break
            writer.write(block)
            remaining -= len(block)
        if remaining or response.read(1):
            raise ManagerError("Downloaded Release asset size does not match the signed manifest")
        writer.flush()
        os.fsync(writer.fileno())
    return provider
