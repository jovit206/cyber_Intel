from pathlib import Path

from conftest import LAB_161, load_module


def test_mongodb_schema_seed_and_three_query_patterns(mongo_database):
    seed = load_module("test_mongo_seed", LAB_161 / "mongodb" / "seed_data.py")
    queries = load_module("test_mongo_queries", LAB_161 / "mongodb" / "queries.py")
    assert seed.seed(mongo_database) == {"students": 10, "courses": 5}
    assert len(queries.courses_for_student(mongo_database, "S001")) == 3
    assert len(queries.students_in_course(mongo_database, "C101")) == 6
    shared = queries.students_sharing_course(mongo_database, "S001")
    assert {student["student_id"] for student in shared} == {
        "S002", "S003", "S004", "S005", "S006", "S007", "S008", "S009", "S010"
    }
    assert {"uq_student_id"} <= set(mongo_database.students.index_information())
    assert {"uq_course_id"} <= set(mongo_database.courses.index_information())
