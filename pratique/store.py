"""Where attempts live: on disk, or in an S3-compatible bucket (DigitalOcean Spaces).

Replays and the leaderboard are the shareable part of Pratique, so they must outlive a container.
`SpacesStore` signs requests with SigV4 using only the standard library; one small JSON object per
attempt, listed and reloaded at startup.

Environment (Spaces):  SPACES_KEY, SPACES_SECRET, SPACES_BUCKET, SPACES_REGION (default nyc3),
optionally SPACES_ENDPOINT (default https://<region>.digitaloceanspaces.com).
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterator, Optional


class Store:
    kind = "none"

    def save(self, attempt: Dict[str, Any]) -> None:
        raise NotImplementedError

    def load_all(self) -> Iterator[Dict[str, Any]]:
        raise NotImplementedError


class DiskStore(Store):
    kind = "disk"

    def __init__(self, root: Path):
        self.root = Path(root) / "attempts"

    def save(self, attempt: Dict[str, Any]) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / f".{attempt['id']}.json.tmp"
            tmp.write_text(json.dumps(attempt, indent=1))
            tmp.replace(self.root / f"{attempt['id']}.json")
        except OSError:
            pass

    def load_all(self) -> Iterator[Dict[str, Any]]:
        if not self.root.exists():
            return
        for p in sorted(self.root.glob("*.json")):
            try:
                yield json.loads(p.read_text())
            except (OSError, ValueError):
                continue


class SpacesStore(Store):
    kind = "spaces"

    def __init__(self, key: str, secret: str, bucket: str, region: str = "nyc3", endpoint: Optional[str] = None):
        self.key, self.secret, self.bucket, self.region = key, secret, bucket, region
        self.endpoint = (endpoint or f"https://{region}.digitaloceanspaces.com").rstrip("/")
        self.host = urllib.parse.urlparse(self.endpoint).netloc

    # -- SigV4 -------------------------------------------------------------------------------

    def _headers(self, method: str, path: str, payload: bytes, query: str, content_type: Optional[str]) -> Dict[str, str]:
        t = datetime.datetime.now(datetime.timezone.utc)
        amz, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(payload).hexdigest()
        headers = {"host": self.host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz}
        if content_type:
            headers["content-type"] = content_type
        signed = ";".join(sorted(headers))
        canonical = "\n".join([method, path, query, "".join(f"{k}:{v}\n" for k, v in sorted(headers.items())), signed, payload_hash])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = ("AWS4" + self.secret).encode()
        for part in (day, self.region, "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={self.key}/{scope}, SignedHeaders={signed}, Signature={sig}"
        return headers

    def _request(self, method: str, key: str = "", *, payload: bytes = b"", params: Optional[Dict[str, str]] = None,
                 content_type: Optional[str] = None, timeout: float = 30) -> tuple:
        path = f"/{self.bucket}" + (f"/{urllib.parse.quote(key)}" if key else "")
        # SigV4 canonicalizes the query sorted by name and percent-encoded; sign exactly what we send
        query = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}" for k, v in sorted((params or {}).items()))
        url = f"{self.endpoint}{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, method=method, data=payload or None,
                                     headers=self._headers(method, path, payload, query, content_type))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    # -- the store ----------------------------------------------------------------------------

    def save(self, attempt: Dict[str, Any]) -> None:
        body = json.dumps(attempt).encode()
        status, _ = self._request("PUT", f"attempts/{attempt['id']}.json", payload=body, content_type="application/json")
        if status not in (200, 201):
            raise OSError(f"spaces put failed: HTTP {status}")

    def get(self, attempt_id: str) -> Optional[Dict[str, Any]]:
        status, body = self._request("GET", f"attempts/{attempt_id}.json")
        return json.loads(body) if status == 200 else None

    def keys(self, prefix: str = "attempts/") -> Iterator[str]:
        token = ""
        while True:
            params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if token:
                params["continuation-token"] = token
            status, body = self._request("GET", params=params)
            if status != 200:
                raise OSError(f"spaces list failed: HTTP {status}")
            root = ET.fromstring(body)
            ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            for c in root.iter(f"{ns}Contents"):
                k = c.find(f"{ns}Key")
                if k is not None and k.text:
                    yield k.text
            nxt = root.find(f"{ns}NextContinuationToken")
            if root.findtext(f"{ns}IsTruncated") == "true" and nxt is not None and nxt.text:
                token = nxt.text
            else:
                return

    def load_all(self) -> Iterator[Dict[str, Any]]:
        for k in self.keys():
            status, body = self._request("GET", k)
            if status == 200:
                try:
                    yield json.loads(body)
                except ValueError:
                    continue


def from_env(default_dir: Path) -> Store:
    key, secret, bucket = os.environ.get("SPACES_KEY"), os.environ.get("SPACES_SECRET"), os.environ.get("SPACES_BUCKET")
    if key and secret and bucket:
        return SpacesStore(key, secret, bucket, os.environ.get("SPACES_REGION") or "nyc3", os.environ.get("SPACES_ENDPOINT"))
    return DiskStore(Path(os.environ.get("PRATIQUE_DATA_DIR") or default_dir))
