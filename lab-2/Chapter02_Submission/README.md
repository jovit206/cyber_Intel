# Chapter 02 - Cloud Computing Primer and Environment Setup

Activities 2.1 and 2.2 from *NoSQL Databases using MongoDB*, including both
"Try It Yourself" exercises.

## Contents

| Folder | Python script | MongoDB data file |
|---|---|---|
| `Activity_2.1/` | `activity_2_1_setup_verification.py` | `activity_2_1_environment_report.json` |
| `Activity_2.1_Try_It_Yourself/` | `activity_2_1_try_it_yourself.py` | `activity_2_1_server_comparison.json` |
| `Activity_2.2/` | `activity_2_2_bookstore_crud.py` | `activity_2_2_books.json` |
| `Activity_2.2_Try_It_Yourself/` | `activity_2_2_try_it_yourself.py` | `activity_2_2_try_it_yourself_books.json` |

Plus `Chapter02_Activities_Report.docx` (all four scripts + their output + screenshots),
`Screenshots/`, `build_report.py` (regenerates the DOCX), and `.env`.

## Setup

```bash
pip install pymongo python-dotenv python-docx

# Start the Docker MongoDB (mongo:7 on host port 27018)
docker run -d -p 27018:27017 --name mongodb-docker mongo:7

# Later:  docker stop mongodb-docker  /  docker start mongodb-docker
```

Then edit `.env` and uncomment `MONGO_URI_ATLAS` with your Atlas connection string
(Atlas -> Database -> Connect -> Drivers -> Python).

Port 27018 is used so the container does not collide with a mongod already
running on 27017. Change `MONGO_URI_LOCAL` in `.env` if you prefer 27017.

## Running

```bash
cd Activity_2.1                  && python activity_2_1_setup_verification.py
cd Activity_2.1_Try_It_Yourself  && python activity_2_1_try_it_yourself.py
cd Activity_2.2                  && python activity_2_2_bookstore_crud.py
cd Activity_2.2_Try_It_Yourself  && python activity_2_2_try_it_yourself.py
```

Each script writes its own JSON export next to itself.

## Rebuilding the report

`build_report.py` re-runs all four scripts, captures their live terminal output,
embeds the source and the JSON, and inserts every screenshot found in
`Screenshots/`. Missing required screenshots are flagged in red in the DOCX and
listed on the console.

```bash
python build_report.py
```

## What lands in MongoDB

| Deployment | Database | Collection | Written by |
|---|---|---|---|
| Docker | `chapter02` | `setup_checks` | Activity 2.1 |
| Docker + Atlas | `chapter02` | `server_comparison` | Activity 2.1 TIY |
| Docker | `bookstore` | `books` | Activity 2.2 |
| Docker | `bookstore` | `books_price_update` | Activity 2.2 TIY |

Activity 2.2 and its "Try It Yourself" use **separate collections** so that the
Compass screenshots for one are not overwritten by running the other.
