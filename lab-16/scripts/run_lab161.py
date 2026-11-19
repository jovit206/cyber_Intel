from __future__ import annotations

import json
import os
import sys
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lab-16.1"))
sys.path.insert(0, str(ROOT / "lab-16.1" / "common"))
sys.path.insert(0, str(ROOT / "lab-16.2"))

from app.cassandra_support import create_cluster
from app.database import LocalhostAddressTranslator
from common.dataset import ENROLLMENTS, STUDENTS, build_expected
from neo4j import GraphDatabase
from pymongo import MongoClient


def load_queries(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load query module at {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    mongo_client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27020"), serverSelectionTimeoutMS=5000)
    cassandra_cluster = create_cluster(
        [host.strip() for host in os.getenv("CASSANDRA_HOSTS", "localhost").split(",") if host.strip()],
        port=int(os.getenv("CASSANDRA_PORT", "9043")),
        address_translator=LocalhostAddressTranslator(),
    )
    cassandra_session = cassandra_cluster.connect()
    neo4j_driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7688"),
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
        max_connection_pool_size=10,
    )
    try:
        mongo_queries = load_queries("university_mongodb_queries", ROOT / "lab-16.1" / "mongodb" / "queries.py")
        mongo_seed = load_queries("university_mongodb_seed", ROOT / "lab-16.1" / "mongodb" / "seed_data.py")
        cassandra_queries_module = load_queries("university_cassandra_queries", ROOT / "lab-16.1" / "cassandra" / "queries.py")
        cassandra_seed = load_queries("university_cassandra_seed", ROOT / "lab-16.1" / "cassandra" / "seed_data.py")
        neo_queries = load_queries("university_neo4j_queries", ROOT / "lab-16.1" / "neo4j" / "queries.py")
        neo_seed = load_queries("university_neo4j_seed", ROOT / "lab-16.1" / "neo4j" / "seed_data.py")
        mongo_database = mongo_client[os.getenv("MONGO_DATABASE", "university_db")]
        print("MongoDB seed:", mongo_seed.seed(mongo_database))
        print("Cassandra seed:", cassandra_seed.seed(cassandra_session))
        neo4j_driver.verify_connectivity()
        with neo4j_driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as graph:
            print("Neo4j seed:", neo_seed.seed(graph))

        cassandra_queries = cassandra_queries_module.EnrollmentQueries(cassandra_session)
        student_id, course_id = "S001", "C101"
        outputs = {
            "dataset": {"students": len(STUDENTS), "courses": 5, "enrollments": sum(map(len, ENROLLMENTS.values()))},
            "student_id": student_id,
            "course_id": course_id,
            "courses_for_student": {
                "mongodb": mongo_queries.courses_for_student(mongo_database, student_id),
                "cassandra": cassandra_queries.courses_for_student(student_id),
                "neo4j": None,
            },
            "students_in_course": {
                "mongodb": mongo_queries.students_in_course(mongo_database, course_id),
                "cassandra": cassandra_queries.students_in_course(course_id),
                "neo4j": None,
            },
            "students_sharing_course": {
                "mongodb": mongo_queries.students_sharing_course(mongo_database, student_id),
                "cassandra": cassandra_queries.students_sharing_course(student_id),
                "neo4j": None,
            },
        }
        with neo4j_driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as graph:
            outputs["courses_for_student"]["neo4j"] = neo_queries.courses_for_student(graph, student_id)
            outputs["students_in_course"]["neo4j"] = neo_queries.students_in_course(graph, course_id)
            outputs["students_sharing_course"]["neo4j"] = neo_queries.students_sharing_course(graph, student_id)
        outputs["consistent"] = {
            "courses_for_student": (
                {row["course_id"] for row in outputs["courses_for_student"]["mongodb"]}
                == {row["course_id"] for row in outputs["courses_for_student"]["cassandra"]}
                == {row["course_id"] for row in outputs["courses_for_student"]["neo4j"]}
            ),
            "students_in_course": (
                {row["student_id"] for row in outputs["students_in_course"]["mongodb"]}
                == {row["student_id"] for row in outputs["students_in_course"]["cassandra"]}
                == {row["student_id"] for row in outputs["students_in_course"]["neo4j"]}
            ),
            "students_sharing_course": (
                {row["student_id"] for row in outputs["students_sharing_course"]["mongodb"]}
                == {row["student_id"] for row in outputs["students_sharing_course"]["cassandra"]}
                == {row["student_id"] for row in outputs["students_sharing_course"]["neo4j"]}
            ),
        }
        if not all(outputs["consistent"].values()):
            raise RuntimeError(f"Polyglot query results differ: {outputs['consistent']}")
        report_path = ROOT / "lab-16.1" / "query_comparison_results.json"
        report_path.write_text(json.dumps(outputs, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"consistent": outputs["consistent"], "report": str(report_path)}, indent=2))
    finally:
        neo4j_driver.close()
        cassandra_session.shutdown()
        cassandra_cluster.shutdown()
        mongo_client.close()


if __name__ == "__main__":
    main()
