from conftest import LAB_161, load_module


def test_neo4j_constraints_seed_relationships_and_three_traversals(neo4j_session):
    seed = load_module("test_neo4j_seed", LAB_161 / "neo4j" / "seed_data.py")
    queries = load_module("test_neo4j_queries", LAB_161 / "neo4j" / "queries.py")
    assert seed.seed(neo4j_session) == {"students": 10, "courses": 5, "enrollments": 24}
    assert len(queries.courses_for_student(neo4j_session, "S001")) == 3
    assert len(queries.students_in_course(neo4j_session, "C101")) == 6
    assert {row["student_id"] for row in queries.students_sharing_course(neo4j_session, "S001")} == {
        "S002", "S003", "S004", "S005", "S006", "S007", "S008", "S009", "S010"
    }
