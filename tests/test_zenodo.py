import json

import httpx
import pytest

from llmanno.core.export.zenodo import SANDBOX, Zenodo, ZenodoError, preregister

META = {
    "title": "Preregistration",
    "description": "<p>Plan</p>",
    "creators": [{"name": "Filler, Simon"}],
    "upload_type": "publication",
    "publication_type": "other",
}


def fake(fail_upload=False):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.content))
        url = str(request.url)
        dep = f"{SANDBOX}/deposit/depositions/7"
        links = {"self": dep, "bucket": "https://sandbox.zenodo.org/api/files/b1",
                 "publish": f"{dep}/actions/publish",
                 "html": "https://sandbox.zenodo.org/deposit/7"}  # fmt: skip
        if request.method == "POST" and url.endswith("/deposit/depositions"):
            return httpx.Response(201, json={"id": 7, "links": links})
        if request.method == "PUT" and url == dep:
            meta = json.loads(request.content)["metadata"]
            meta["prereserve_doi"] = {"doi": "10.5072/zenodo.7"}
            return httpx.Response(200, json={"id": 7, "links": links, "metadata": meta})
        if request.method == "PUT" and "/files/b1/" in url:
            return httpx.Response(500 if fail_upload else 201, json={})
        if request.method == "POST" and url.endswith("/actions/publish"):
            return httpx.Response(202, json={"id": 7, "doi": "10.5072/zenodo.7", "links": links})
        if request.method == "DELETE" and url == dep:
            return httpx.Response(204)
        return httpx.Response(404)

    return calls, httpx.MockTransport(handler)


def test_draft_by_default_reserves_doi_and_uploads(tmp_path):
    (tmp_path / "plan.md").write_text("# Plan", encoding="utf-8")
    calls, transport = fake()
    client = Zenodo("t", SANDBOX, transport=transport)
    result = preregister([tmp_path / "plan.md"], META, client=client)
    assert result == {"id": 7, "doi": "10.5072/zenodo.7", "url": "https://sandbox.zenodo.org/deposit/7",
                      "published": False, "sandbox": True}  # fmt: skip
    assert [c[0] for c in calls] == ["POST", "PUT", "PUT"]  # kein Veröffentlichen
    assert json.loads(calls[1][2])["metadata"]["prereserve_doi"] is True
    assert calls[2][1].endswith("/files/b1/plan.md") and calls[2][2] == b"# Plan"


def test_publish_only_when_asked(tmp_path):
    (tmp_path / "plan.md").write_text("x", encoding="utf-8")
    calls, transport = fake()
    result = preregister([tmp_path / "plan.md"], META, publish=True,
                         client=Zenodo("t", SANDBOX, transport=transport))  # fmt: skip
    assert calls[-1][1].endswith("/actions/publish") and result["published"]


def test_failed_upload_deletes_draft(tmp_path):
    (tmp_path / "plan.md").write_text("x", encoding="utf-8")
    calls, transport = fake(fail_upload=True)
    with pytest.raises(ZenodoError, match="HTTP 500"):
        preregister([tmp_path / "plan.md"], META, client=Zenodo("t", SANDBOX, transport=transport))
    assert calls[-1][0] == "DELETE"


def test_refuses_incomplete_metadata_and_missing_files(tmp_path):
    calls, transport = fake()
    client = Zenodo("t", SANDBOX, transport=transport)
    with pytest.raises(ZenodoError, match="Dateien fehlen"):
        preregister([tmp_path / "fehlt.md"], META, client=client)
    (tmp_path / "plan.md").write_text("x", encoding="utf-8")
    with pytest.raises(ZenodoError, match="creators"):
        preregister([tmp_path / "plan.md"], META | {"creators": []}, client=client)
    assert calls == []


def test_orcid_url_is_reduced_to_id(tmp_path):
    (tmp_path / "plan.md").write_text("x", encoding="utf-8")
    calls, transport = fake()
    meta = META | {
        "creators": [{"name": "Filler, Simon", "orcid": "https://orcid.org/0009-0008-8736-8831"}]
    }
    preregister([tmp_path / "plan.md"], meta, client=Zenodo("t", SANDBOX, transport=transport))
    sent = json.loads(calls[1][2])["metadata"]["creators"][0]
    assert sent["orcid"] == "0009-0008-8736-8831"
