from conftest import LAB_161, load_module


def test_cassandra_schema_seed_and_three_query_patterns(cassandra_session):
    seed = load_module("test_cassandra_seed", LAB_161 / "cassandra" / "seed_data.py")
    queries_module = load_module("test_cassandra_queries", LAB_161 / "cassandra" / "queries.py")
    result = seed.seed(cassandra_session)
    assert result == {"students": 10, "courses": 5, "enrollments": 24}
    queries = queries_module.EnrollmentQueries(cassandra_session)
    assert len(queries.courses_for_student("S001")) == 3
    assert len(queries.students_in_course("C101")) == 6
    assert {row["student_id"] for row in queries.students_sharing_course("S001")} == {
        "S002", "S003", "S004", "S005", "S006", "S007", "S008", "S009", "S010"
    }
