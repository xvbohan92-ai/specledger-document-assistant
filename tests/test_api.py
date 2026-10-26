from fastapi.testclient import TestClient

from specledger.api import create_app


def test_upload_review_query_and_page_provenance(ledger):
    with TestClient(create_app(ledger.settings, ledger=ledger)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/meta").json()["backend"]["ocr"] is False
        path = ledger.settings.data / "demo/pdfs/dev-01.pdf"
        result = client.post(
            "/api/upload",
            files={"file": ("ratings.pdf", path.read_bytes(), "application/pdf")},
            data={"source": "Demo PDF; self-authored"},
        )
        assert result.status_code == 200
        doc = result.json()["document"]
        assert "pdf_path" not in doc
        assert client.post("/api/query", json={}).json()["count"] == 0
        approved = client.post(f"/api/documents/{doc['id']}/approve", json={"revision": 1})
        assert approved.json()["status"] == "approved"
        query = client.post(
            "/api/query", json={"filters": [{"field": "mass_kg", "raw_value": "2 kg"}]}
        ).json()
        assert query["count"] == 1
        assert query["results"][0]["evidence"]["mass_kg"]["evidence"]["page"] == 2
        image = client.get(f"/api/documents/{doc['id']}/pages/2")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert client.get(f"/api/documents/{doc['id']}/pages/3").status_code == 404
        assert client.get(f"/api/documents/{doc['id']}/pdf").status_code == 200
        assert client.get("/api/evaluation").json()["available"] is False
        assert "object-src 'none'" in client.get("/").headers["content-security-policy"]


def test_invalid_upload_does_not_create_records(ledger):
    with TestClient(create_app(ledger.settings, ledger=ledger)) as client:
        response = client.post(
            "/api/upload", files={"file": ("bad.pdf", b"no pdf", "application/pdf")}, data={"source": "test"}
        )
        assert response.status_code == 400
        assert client.get("/api/documents").json() == []
        assert (
            client.post(
                "/api/query", json={"filters": [{"field": "power_w; DROP TABLE records", "raw_value": "1 W"}]}
            ).status_code
            == 422
        )


def test_reparse_endpoint_returns_read_only_history(ledger):
    doc = ledger.ingest(ledger.settings.data / "demo/pdfs/dev-01.pdf")["document"]
    with TestClient(create_app(ledger.settings, ledger=ledger)) as client:
        response = client.post(
            f"/api/documents/{doc['id']}/reparse",
            json={"revision": 1, "reason": "Refresh the extracted fields"},
        )
        assert response.status_code == 200
        assert response.json()["revision"] == 2
        old = client.get(f"/api/documents/{doc['id']}/versions/1").json()
        assert old["historical"] is True
        assert old["latest_revision"] == 2
        assert "pdf_path" not in old
        assert (
            client.post(
                f"/api/documents/{doc['id']}/reparse", json={"revision": 1, "reason": "Stale request"}
            ).status_code
            == 400
        )
