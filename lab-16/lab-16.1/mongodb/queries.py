def courses_for_student(database, student_id: str) -> list[dict]:
    student = database.students.find_one({"student_id": student_id}, {"_id": 0, "course_ids": 1})
    if not student:
        return []
    return list(database.courses.find(
        {"course_id": {"$in": student["course_ids"]}},
        {"_id": 0},
    ).sort("course_id", 1))


def students_in_course(database, course_id: str) -> list[dict]:
    course = database.courses.find_one({"course_id": course_id}, {"_id": 0, "student_ids": 1})
    if not course:
        return []
    return list(database.students.find(
        {"student_id": {"$in": course["student_ids"]}},
        {"_id": 0, "course_ids": 0},
    ).sort("student_id", 1))


def students_sharing_course(database, student_id: str) -> list[dict]:
    student = database.students.find_one({"student_id": student_id}, {"_id": 0, "course_ids": 1})
    if not student:
        return []
    return list(database.students.find(
        {"student_id": {"$ne": student_id}, "course_ids": {"$in": student["course_ids"]}},
        {"_id": 0, "course_ids": 0},
    ).sort("student_id", 1))
