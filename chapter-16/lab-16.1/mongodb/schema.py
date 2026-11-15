from pymongo import ASCENDING


def create_schema(database):
    database.students.create_index([("student_id", ASCENDING)], unique=True, name="uq_student_id")
    database.courses.create_index([("course_id", ASCENDING)], unique=True, name="uq_course_id")
    database.courses.create_index([("student_ids", ASCENDING)], name="ix_course_student_ids")
    return ["students", "courses"]
