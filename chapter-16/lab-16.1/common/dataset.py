"""Canonical university dataset shared by all three storage adapters."""

STUDENTS = [
    {
        "student_id": f"S{i:03d}",
        "name": name,
        "email": f"{name.split()[0].lower()}{i}@university.example",
        "department": department,
    }
    for i, (name, department) in enumerate([
        ("Ava Patel", "Computer Science"), ("Noah Kim", "Mathematics"),
        ("Mia Garcia", "Physics"), ("Liam Chen", "Computer Science"),
        ("Sophia Brown", "Biology"), ("Ethan Wilson", "Mathematics"),
        ("Isabella Davis", "Physics"), ("Lucas Miller", "Computer Science"),
        ("Amelia Moore", "Biology"), ("Mason Taylor", "Mathematics"),
    ], start=1)
]

COURSES = [
    {"course_id": "C101", "course_name": "Introduction to Python", "instructor": "Dr. Rivera", "credits": 3},
    {"course_id": "C202", "course_name": "Database Systems", "instructor": "Dr. Singh", "credits": 4},
    {"course_id": "C303", "course_name": "Applied Statistics", "instructor": "Dr. Chen", "credits": 3},
    {"course_id": "C404", "course_name": "Machine Learning", "instructor": "Dr. Okafor", "credits": 4},
    {"course_id": "C505", "course_name": "Research Methods", "instructor": "Dr. Martin", "credits": 2},
]

ENROLLMENTS = {
    "S001": ["C101", "C202", "C303"],
    "S002": ["C101", "C303"],
    "S003": ["C202", "C404"],
    "S004": ["C101", "C202", "C404"],
    "S005": ["C303", "C505"],
    "S006": ["C101", "C303", "C505"],
    "S007": ["C202", "C404", "C505"],
    "S008": ["C101", "C202"],
    "S009": ["C303", "C404"],
    "S010": ["C101", "C505"],
}


def build_expected():
    student_by_id = {student["student_id"]: student for student in STUDENTS}
    course_by_id = {course["course_id"]: course for course in COURSES}
    return {
        "students": [
            {**student, "course_ids": ENROLLMENTS[student["student_id"]]}
            for student in STUDENTS
        ],
        "courses": [
            {**course, "student_ids": [
                student_id for student_id, course_ids in ENROLLMENTS.items()
                if course["course_id"] in course_ids
            ]}
            for course in COURSES
        ],
        "student_by_id": student_by_id,
        "course_by_id": course_by_id,
    }
