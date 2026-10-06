from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.frontend import FrontendFiles


def test_html_is_revalidated_and_old_chunks_remain_accessible(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text('<script src="/assets/new.js"></script>')
    (tmp_path / "assets" / "old.js").write_text('export const version = "old";')
    (tmp_path / "assets" / "new.js").write_text('export const version = "new";')
    app = FastAPI()
    app.mount("/", FrontendFiles(directory=tmp_path, html=True))
    with TestClient(app) as client:
        for path in ["/", "/index.html"]:
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            cached = client.get(path, headers={"If-None-Match": response.headers["etag"]})
            assert cached.status_code == 304
            assert cached.headers["cache-control"] == "no-cache"
        old = client.get("/assets/old.js")
        assert old.status_code == 200
        assert '"old"' in old.text
        assert "javascript" in old.headers["content-type"]
        assert client.get("/assets/missing.js").status_code == 404
