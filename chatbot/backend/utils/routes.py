"""Enumerate the application's HTTP routes across FastAPI versions.

FastAPI 0.16x keeps each ``include_router`` call as a wrapper object in ``app.routes`` instead of
copying the routes, so ``{route.path for route in app.routes}`` no longer lists ``/api/...`` paths.
"""
from __future__ import annotations

from typing import Any, Iterator, NamedTuple


class RouteInfo(NamedTuple):
    path: str
    methods: frozenset[str]
    tags: tuple[str, ...]
    endpoint: Any


def iter_routes(app) -> Iterator[RouteInfo]:
    yield from _walk(getattr(app, "routes", []))


def _walk(routes) -> Iterator[RouteInfo]:
    for route in routes:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            for context in candidates():
                if callable(getattr(context, "effective_candidates", None)):  # nested include_router
                    yield from _walk([context])
                    continue
                if not hasattr(context, "path"):
                    continue
                original = getattr(context, "original_route", None)
                tags = getattr(context, "tags", None) or getattr(original, "tags", None) or []
                methods = getattr(context, "methods", None) or getattr(original, "methods", None) or ()
                yield RouteInfo(context.path, frozenset(methods), tuple(str(t) for t in tags), context.endpoint)
            continue
        path = getattr(route, "path", None)
        if path:
            yield RouteInfo(path, frozenset(getattr(route, "methods", None) or ()),
                            tuple(str(t) for t in (getattr(route, "tags", None) or [])), getattr(route, "endpoint", None))


def route_paths(app) -> set[str]:
    return {route.path for route in iter_routes(app)}
