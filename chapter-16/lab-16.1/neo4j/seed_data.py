from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.env import load_chapter16_env

load_chapter16_env()

from common.dataset import build_expected
from neo4j import GraphDatabase

SCHEMA = [
    "CREATE CONSTRAINT student_id_unique IF NOT EXISTS FOR (s:Student) REQUIRE s.student_id IS UNIQUE",
    "CREATE CONSTRAINT course_id_unique IF NOT EXISTS FOR (c:Course) REQUIRE c.course_id IS UNIQUE",
    "CREATE INDEX student_department IF NOT EXISTS FOR (s:Student) ON (s.department)",
    "CREATE INDEX course_name IF NOT EXISTS FOR (c:Course) ON (c.course_name)",
]


def seed(session):
    for statement in SCHEMA:
        session.run(statement).consume()
    data = build_expected()
    session.run("""
        UNWIND $students AS row
        MERGE (s:Student {student_id: row.student_id})
        SET s.name = row.name, s.email = row.email, s.department = row.department
    """, students=data["students"]).consume()
    session.run("""
        UNWIND $courses AS row
        MERGE (c:Course {course_id: row.course_id})
        SET c.course_name = row.course_name, c.instructor = row.instructor, c.credits = row.credits
    """, courses=data["courses"]).consume()
    edges = [
        {"student_id": student["student_id"], "course_id": course_id}
        for student in data["students"] for course_id in student["course_ids"]
    ]
    session.run("""
        UNWIND $edges AS row
        MATCH (s:Student {student_id: row.student_id})
        MATCH (c:Course {course_id: row.course_id})
        MERGE (s)-[:ENROLLED_IN]->(c)
    """, edges=edges).consume()
    return {"students": len(data["students"]), "courses": len(data["courses"]), "enrollments": len(edges)}


def main():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7688")
    auth = (os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", ""))
    driver = GraphDatabase.driver(uri, auth=auth, max_connection_pool_size=10)
    try:
        driver.verify_connectivity()
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            print(seed(session))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
