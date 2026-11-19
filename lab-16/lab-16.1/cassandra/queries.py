from cassandra.query import PreparedStatement


class EnrollmentQueries:
    def __init__(self, session):
        self.session = session
        self.student_courses = session.prepare(
            "SELECT course_id, course_name, instructor, credits FROM university_data.student_courses WHERE student_id = ?"
        )
        self.course_students = session.prepare(
            "SELECT student_id, student_name, department FROM university_data.course_students WHERE course_id = ?"
        )

    def courses_for_student(self, student_id: str) -> list[dict]:
        return [dict(row._asdict()) for row in self.session.execute(self.student_courses, (student_id,))]

    def students_in_course(self, course_id: str) -> list[dict]:
        return [dict(row._asdict()) for row in self.session.execute(self.course_students, (course_id,))]

    def students_sharing_course(self, student_id: str) -> list[dict]:
        shared = {}
        for enrollment in self.session.execute(self.student_courses, (student_id,)):
            for student in self.session.execute(self.course_students, (enrollment.course_id,)):
                if student.student_id != student_id:
                    shared[student.student_id] = {
                        "student_id": student.student_id,
                        "name": student.student_name,
                        "department": student.department,
                    }
        return [shared[key] for key in sorted(shared)]
