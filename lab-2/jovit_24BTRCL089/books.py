"""Seed data for the Chapter 2 bookstore activities (Jovit Chawla, 24BTRCL089).

Titles, authors, first-publication years and publishers are those of real, well-known books. The `price_inr`
and `stock` values are store values chosen for this lab exercise, not market data.
"""

BOOKS = [
    {"isbn": "978-0132350884", "title": "Clean Code", "authors": ["Robert C. Martin"], "year": 2008,
     "publisher": {"name": "Prentice Hall", "country": "USA"}, "tags": ["software engineering", "best practices"],
     "price_inr": 650.0, "stock": 12},
    {"isbn": "978-0201616224", "title": "The Pragmatic Programmer", "authors": ["Andrew Hunt", "David Thomas"],
     "year": 1999, "publisher": {"name": "Addison-Wesley", "country": "USA"},
     "tags": ["software engineering", "career"], "price_inr": 720.0, "stock": 7},
    {"isbn": "978-1449373320", "title": "Designing Data-Intensive Applications", "authors": ["Martin Kleppmann"],
     "year": 2017, "publisher": {"name": "O'Reilly Media", "country": "USA"},
     "tags": ["databases", "distributed systems"], "price_inr": 1450.0, "stock": 5},
    {"isbn": "978-0262033848", "title": "Introduction to Algorithms", "authors": ["Thomas H. Cormen", "Charles E. Leiserson",
     "Ronald L. Rivest", "Clifford Stein"], "year": 1990, "publisher": {"name": "MIT Press", "country": "USA"},
     "tags": ["algorithms", "textbook"], "price_inr": 1250.0, "stock": 9},
    {"isbn": "978-0201835953", "title": "The Mythical Man-Month", "authors": ["Frederick P. Brooks Jr."], "year": 1975,
     "publisher": {"name": "Addison-Wesley", "country": "USA"}, "tags": ["software engineering", "management"],
     "price_inr": 540.0, "stock": 4},
    {"isbn": "978-0262035613", "title": "Deep Learning", "authors": ["Ian Goodfellow", "Yoshua Bengio", "Aaron Courville"],
     "year": 2016, "publisher": {"name": "MIT Press", "country": "USA"}, "tags": ["machine learning", "textbook"],
     "price_inr": 1600.0, "stock": 6},
    {"isbn": "978-0201485677", "title": "Refactoring", "authors": ["Martin Fowler"], "year": 1999,
     "publisher": {"name": "Addison-Wesley", "country": "USA"}, "tags": ["software engineering", "best practices"],
     "price_inr": 820.0, "stock": 3},
    {"isbn": "978-1934356920", "title": "Seven Databases in Seven Weeks", "authors": ["Eric Redmond", "Jim R. Wilson"],
     "year": 2012, "publisher": {"name": "Pragmatic Bookshelf", "country": "USA"}, "tags": ["databases", "nosql"],
     "price_inr": 780.0, "stock": 8},
    {"isbn": "978-1593276034", "title": "Python Crash Course", "authors": ["Eric Matthes"], "year": 2015,
     "publisher": {"name": "No Starch Press", "country": "USA"}, "tags": ["python", "beginner"],
     "price_inr": 560.0, "stock": 15},
    {"isbn": "978-8173711466", "title": "Wings of Fire", "authors": ["A. P. J. Abdul Kalam", "Arun Tiwari"], "year": 1999,
     "publisher": {"name": "Universities Press", "country": "India"}, "tags": ["autobiography", "india"],
     "price_inr": 299.0, "stock": 20},
]
