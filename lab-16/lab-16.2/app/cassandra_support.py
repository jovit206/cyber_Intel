from __future__ import annotations

import platform
import sys

WINDOWS_MODERN_PYTHON = platform.system() == "Windows" and sys.version_info >= (3, 12)

if WINDOWS_MODERN_PYTHON:
    from gevent import monkey

    monkey.patch_all(ssl=False)

from cassandra.cluster import Cluster

if WINDOWS_MODERN_PYTHON:
    from cassandra.io.geventreactor import GeventConnection


def create_cluster(*args, **kwargs):
    if WINDOWS_MODERN_PYTHON:
        kwargs["connection_class"] = GeventConnection
    return Cluster(*args, **kwargs)
