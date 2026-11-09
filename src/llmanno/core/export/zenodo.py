"""Präregistrierung bei Zenodo über die REST-API: Entwurf anlegen, Dateien hochladen, DOI
reservieren; veröffentlichen nur auf ausdrücklichen Wunsch.

Veröffentlichte Datensätze lassen sich bei Zenodo nicht löschen. Deshalb legt `preregister`
standardmäßig nur einen Entwurf an, und die Sandbox (Test-DOIs 10.5072) ist voreingestellt.
Der Token liegt im Schlüsselbund (Dienst „llmanno“, Name = API-Adresse) und braucht die
Bereiche deposit:write und deposit:actions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import keyring

SANDBOX = "https://sandbox.zenodo.org/api"
PRODUCTION = "https://zenodo.org/api"
KEYRING_SERVICE = "llmanno"
REQUIRED = ("title", "description", "creators", "upload_type")


class ZenodoError(Exception):
    pass


def token(base: str) -> str:
    value = keyring.get_password(KEYRING_SERVICE, base)
    if not value:
        raise ZenodoError(
            f"Kein Zenodo-Token für {base} im Schlüsselbund (Dienst „{KEYRING_SERVICE}“, "
            f"Name „{base}“)."
        )
    return value


class Zenodo:
    def __init__(
        self, api_token: str, base: str = SANDBOX, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.base = base
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_token}"}, timeout=120, transport=transport
        )

    def _call(self, method: str, url: str, **kwargs: Any) -> dict:
        response = self._client.request(method, url, **kwargs)
        if not response.is_success:
            raise ZenodoError(
                f"Zenodo {method} {url}: HTTP {response.status_code} {response.text[:500]}"
            )
        return response.json() if response.content else {}

    def create_draft(self, metadata: Mapping[str, Any]) -> dict:
        """Neuer Entwurf mit Metadaten und reservierter DOI (erst beim Veröffentlichen gültig)."""
        missing = [k for k in REQUIRED if not metadata.get(k)]
        if missing:
            raise ZenodoError(f"Metadaten unvollständig: {', '.join(missing)}")
        draft = self._call("POST", f"{self.base}/deposit/depositions", json={})
        # Leere Felder (etwa eine noch nicht eingetragene ORCID) lehnt Zenodo ab
        creators = [{k: v for k, v in c.items() if v} for c in metadata["creators"]]
        for c in creators:  # Zenodo will die nackte ORCID, nicht die URL
            if "orcid" in c:
                c["orcid"] = c["orcid"].removeprefix("https://orcid.org/").strip("/")
        meta = dict(metadata) | {"creators": creators, "prereserve_doi": True}
        return self._call("PUT", draft["links"]["self"], json={"metadata": meta})

    def upload(self, draft: Mapping[str, Any], path: Path) -> dict:
        with path.open("rb") as f:
            return self._call("PUT", f"{draft['links']['bucket']}/{path.name}", content=f.read())

    def publish(self, draft: Mapping[str, Any]) -> dict:
        """Unwiderruflich: Der Datensatz wird öffentlich und lässt sich nicht mehr löschen."""
        return self._call("POST", draft["links"]["publish"])

    def delete_draft(self, draft: Mapping[str, Any]) -> None:
        self._call("DELETE", draft["links"]["self"])

    def close(self) -> None:
        self._client.close()


def reserved_doi(record: Mapping[str, Any]) -> str | None:
    return (record.get("metadata", {}).get("prereserve_doi") or {}).get("doi")


def preregister(
    files: Sequence[Path],
    metadata: Mapping[str, Any],
    *,
    base: str = SANDBOX,
    publish: bool = False,
    client: Zenodo | None = None,
) -> dict:
    """Legt einen Entwurf an und lädt die Dateien hoch; veröffentlicht nur mit `publish=True`.

    Liefert Kennung, reservierte DOI, Link und ob veröffentlicht wurde. Scheitert ein Upload,
    wird der Entwurf wieder gelöscht, damit keine halben Entwürfe liegen bleiben.
    """
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise ZenodoError(f"Dateien fehlen: {', '.join(missing)}")
    zenodo = client or Zenodo(token(base), base)
    draft = zenodo.create_draft(metadata)
    try:
        for path in files:
            zenodo.upload(draft, path)
    except ZenodoError:
        zenodo.delete_draft(draft)
        raise
    record = zenodo.publish(draft) if publish else draft
    return {
        "id": record.get("id"),
        "doi": record.get("doi") or reserved_doi(draft),
        "url": record.get("links", {}).get("html"),
        "published": publish,
        "sandbox": base == SANDBOX,
    }
