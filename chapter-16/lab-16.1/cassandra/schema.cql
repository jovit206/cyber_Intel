CREATE KEYSPACE IF NOT EXISTS university_data
WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

CREATE TABLE IF NOT EXISTS university_data.student_courses (
    student_id text,
    course_id text,
    course_name text,
    instructor text,
    credits int,
    PRIMARY KEY ((student_id), course_id)
) WITH CLUSTERING ORDER BY (course_id ASC);

CREATE TABLE IF NOT EXISTS university_data.course_students (
    course_id text,
    student_id text,
    student_name text,
    department text,
    PRIMARY KEY ((course_id), student_id)
) WITH CLUSTERING ORDER BY (student_id ASC);
