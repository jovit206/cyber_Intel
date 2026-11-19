COURSES_FOR_STUDENT = """
MATCH (:Student {student_id: $student_id})-[:ENROLLED_IN]->(c:Course)
RETURN c.course_id AS course_id, c.course_name AS course_name,
       c.instructor AS instructor, c.credits AS credits
ORDER BY course_id
"""

STUDENTS_IN_COURSE = """
MATCH (s:Student)-[:ENROLLED_IN]->(:Course {course_id: $course_id})
RETURN s.student_id AS student_id, s.name AS name, s.department AS department
ORDER BY student_id
"""

STUDENTS_SHARING_COURSE = """
MATCH (target:Student {student_id: $student_id})-[:ENROLLED_IN]->(c:Course)<-[:ENROLLED_IN]-(other:Student)
WHERE other.student_id <> target.student_id
RETURN other.student_id AS student_id, other.name AS name, other.department AS department,
       collect(DISTINCT c.course_id) AS shared_course_ids
ORDER BY student_id
"""


def _query(session, cypher: str, **params) -> list[dict]:
    return [record.data() for record in session.run(cypher, **params)]


def courses_for_student(session, student_id: str) -> list[dict]:
    return _query(session, COURSES_FOR_STUDENT, student_id=student_id)


def students_in_course(session, course_id: str) -> list[dict]:
    return _query(session, STUDENTS_IN_COURSE, course_id=course_id)


def students_sharing_course(session, student_id: str) -> list[dict]:
    return _query(session, STUDENTS_SHARING_COURSE, student_id=student_id)
