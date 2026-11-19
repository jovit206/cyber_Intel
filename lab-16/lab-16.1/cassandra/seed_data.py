from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.env import load_chapter16_env

load_chapter16_env()

from common.dataset import ENROLLMENTS, build_expected
from app.database import LocalhostAddressTranslator
from app.cassandra_support import create_cluster


def seed(session):
    session.execute("""
        CREATE KEYSPACE IF NOT EXISTS university_data
        WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}
    """)
    session.set_keyspace("university_data")
    session.execute("""CREATE TABLE IF NOT EXISTS student_courses (
        student_id text, course_id text, course_name text, instructor text, credits int,
        PRIMARY KEY ((student_id), course_id))""")
    session.execute("""CREATE TABLE IF NOT EXISTS course_students (
        course_id text, student_id text, student_name text, department text,
        PRIMARY KEY ((course_id), student_id))""")
    student_insert = session.prepare(
        "INSERT INTO student_courses (student_id, course_id, course_name, instructor, credits) VALUES (?, ?, ?, ?, ?)"
    )
    course_insert = session.prepare(
        "INSERT INTO course_students (course_id, student_id, student_name, department) VALUES (?, ?, ?, ?)"
    )
    expected = build_expected()
    for student in expected["students"]:
        for course_id in student["course_ids"]:
            course = expected["course_by_id"][course_id]
            session.execute(student_insert, (
                student["student_id"], course_id, course["course_name"],
                course["instructor"], course["credits"],
            ))
            session.execute(course_insert, (
                course_id, student["student_id"], student["name"], student["department"],
            ))
    return {
        "students": len(expected["students"]),
        "courses": len(expected["courses"]),
        "enrollments": sum(map(len, ENROLLMENTS.values())),
    }


def main():
    hosts = [host.strip() for host in os.getenv("CASSANDRA_HOSTS", "localhost").split(",") if host.strip()]
    cluster = create_cluster(
        hosts,
        port=int(os.getenv("CASSANDRA_PORT", "9043")),
        address_translator=LocalhostAddressTranslator()
        if all(host in {"localhost", "127.0.0.1"} for host in hosts) else None,
    )
    session = cluster.connect()
    try:
        print(seed(session))
    finally:
        session.shutdown()
        cluster.shutdown()


if __name__ == "__main__":
    main()
