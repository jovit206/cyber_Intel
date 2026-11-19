CREATE CONSTRAINT student_id_unique IF NOT EXISTS
FOR (s:Student) REQUIRE s.student_id IS UNIQUE;

CREATE CONSTRAINT course_id_unique IF NOT EXISTS
FOR (c:Course) REQUIRE c.course_id IS UNIQUE;

CREATE INDEX student_department IF NOT EXISTS
FOR (s:Student) ON (s.department);

CREATE INDEX course_name IF NOT EXISTS
FOR (c:Course) ON (c.course_name);
