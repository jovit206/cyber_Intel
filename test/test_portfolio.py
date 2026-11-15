import pytest

from fastapi.testclient import TestClient

from backend.main import PORTFOLIO_DOCUMENTS, app


client = TestClient(app)


def test_portfolio_document_serves_allowlisted_project_readme():
    response = client.get("/portfolio/docs/distributed-infrastructure/readme")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "replica set" in response.text.lower()


def test_portfolio_document_rejects_unknown_resource():
    response = client.get("/portfolio/docs/distributed-infrastructure/not-allowlisted")

    assert response.status_code == 404


def test_portfolio_document_serves_verified_benchmark_report():
    response = client.get("/portfolio/docs/database-reliability/benchmarks")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "documents" in response.text.lower()


@pytest.mark.parametrize(("project", "resource"), PORTFOLIO_DOCUMENTS)
def test_every_portfolio_resource_link_resolves(project, resource):
    response = client.get(f"/portfolio/docs/{project}/{resource}")

    assert response.status_code == 200
    assert response.content
