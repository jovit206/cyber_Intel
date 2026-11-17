"""Live course index assembled from registered application routes and local services."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from backend.routes.admin import _admin_user

router = APIRouter(tags=["Course tour"])

_CHAPTERS = [
    (7, "Schema Analysis and Activity Monitoring", "/api/operations/overview"),
    (8, "Reliability and Inventory", "/api/lab8/status"),
    (9, "Sharding and Horizontal Scalability", "/api/lab9/latency"),
    (10, "Banking and Inventory Workflows", "/api/lab10/overview"),
    (11, "Reliability and Transactions", "/api/chapter11/status"),
    (12, "No separate Chapter 12 workspace is registered in this application", None),
    (13, "Deployment and Infrastructure", "/api/ops/overview"),
    (14, "Monitoring and Backup", "/api/ops/metrics/monitoring?minutes=60"),
    (15, "Multi-Tenant API", "/api/cases/me"),
    (16, "Polyglot Database Engineering", "/api/chapter16/status"),
    (17, "Vector Search and RAG", "/api/rag/status"),
    (18, "Threat Intelligence Capstone", "/api/chapter18/status"),
]


@router.get("/api/tour")
def course_tour(request: Request, actor: dict = Depends(_admin_user)) -> dict:
    del actor
    chapters = []
    for number, description, endpoint in _CHAPTERS:
        # Status is deliberately a local declaration; it does not make an external
        # health request or imply that an optional service is running.
        available = bool(endpoint and endpoint.split("?")[0] in {route.path for route in request.app.routes})
        chapters.append({
            "chapter": number,
            "description": description,
            "status": "integrated" if available else "not_integrated",
            "docker_image": None,
            "navigation": f"/?view=exercise-{number}",
            "lab_readiness": "check_live_status" if available else "unavailable",
            "status_endpoint": endpoint if available else None,
        })
    return {"chapters": chapters, "note": "Service readiness must be checked against each chapter's live status endpoint."}


@router.get("/tour", include_in_schema=False)
def course_tour_page(actor: dict = Depends(_admin_user)):
    del actor
    path = Path(__file__).resolve().parents[2] / "public" / "tour.html"
    from fastapi.responses import HTMLResponse
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})
    return HTMLResponse("<main><h1>Course Tour</h1><p>Tour page assets are unavailable.</p></main>", status_code=503)
