#!/usr/bin/env python3
"""Verify that the MongoDB exporter is reachable and reports a connected server."""

from __future__ import annotations

import re
from urllib.request import urlopen


def main() -> None:
    with urlopen("http://mongodb-exporter:9216/metrics", timeout=10) as response:
        metrics = response.read().decode("utf-8")

    values = re.findall(r"^mongodb_up(?:\{[^}]*\})?\s+([0-9.]+)$", metrics, re.MULTILINE)
    if "mongodb_up" not in metrics or not values or any(value != "1" for value in values):
        raise RuntimeError("Exporter did not report mongodb_up 1; inspect its logs and MongoDB credentials.")
    print(f"Exporter scrape succeeded: mongodb_up={','.join(values)}")


if __name__ == "__main__":
    main()
