# Employee Attendance & Analytics API

A production-focused backend service built with FastAPI and MongoDB to manage employee attendance, shift records, and operational analytics at scale. This project is designed to handle real-world constraints such as overnight shifts, timezone-aware attendance logic, audit trails, and performance-sensitive aggregation queries.

The system not only exposes a clean REST API but also demonstrates strong engineering judgment in validation, indexing, query optimization, and edge-case handling—qualities that matter in real-world backend engineering roles.

---

## Why this project matters

Employee attendance is one of the most common operational systems in any organization, but it is rarely simple. Real attendance workflows require:

- accurate handling of shift boundaries and overnight shifts
- timezone-aware calculations
- prevention of duplicate entries and invalid updates
- administrative override support with traceability
- analytics that scale efficiently over large datasets

This application addresses those challenges with a clean API, MongoDB-native aggregation, and a strong focus on reliability and performance.

---

## Core capabilities

### 1. Employee lifecycle management
- Create employees with strict validation
- Enforce unique employee codes
- Support department-based filtering and pagination
- Maintain employee metadata like department, shift timings, and joining date

### 2. Attendance tracking
- Punch-in and punch-out workflows
- Support for presence states such as PRESENT, WFH, and ON_DUTY
- Validation for invalid or duplicate entries
- Handling of overnight shift cases and time-window integrity

### 3. Admin-led regularization
- Partial record updates for attendance entries
- Maintain a nested `history` audit trail
- Track who changed what and why
- Prevent invalid updates such as nullified required values or impossible time ranges

### 4. Analytics engine
- Monthly employee attendance summaries
- Department-level summary insights
- Late-minute leaderboard ranking
- Department attendance trend analysis with 7-day moving averages
- MongoDB aggregation-first approach to avoid expensive Python-side aggregation

### 5. Performance and observability
- Startup index creation for faster lookups and query execution
- `GET /admin/explain/{endpoint}` for execution profiling
- Native MongoDB filtering, sorting, and pagination using `.skip().limit()`
- Query validation and structured API responses for debugging and optimization

---

## Technical stack

- FastAPI for REST API development
- MongoDB with PyMongo for persistence and analytics
- Pydantic v2 for strict request validation and schema enforcement
- Python 3 for backend logic and business rules
- Uvicorn for serving the application
- dotenv for environment configuration

---

## Architecture overview

The service follows a practical backend architecture:

- API layer: FastAPI endpoints for employees, attendance, analytics, and admin diagnostics
- Validation layer: Pydantic models enforce business constraints before data reaches MongoDB
- Persistence layer: MongoDB stores employee records and attendance logs
- Analytics layer: Aggregation pipelines perform summary and trend calculations in the database layer
- Admin layer: Explain endpoint exposes execution metrics to validate query efficiency

This design keeps business logic close to the database and reduces the need for slow in-memory processing.

---

## Business logic implemented

The project includes meaningful production-grade rules, including:

- employee codes must follow a strict pattern: `EMP####` / `EMP######`
- email addresses are validated with regex rules
- shift start and shift end times cannot be equal
- attendance timestamps must be valid epoch milliseconds
- punch-out must occur after punch-in and remain within a valid time range
- overnight shifts are interpreted correctly based on shift boundaries
- attendance dates are computed with timezone-aware logic using IST/UTC conversion
- the same employee cannot have duplicate attendance records for the same date
- admin changes are recorded in an immutable-style nested history log

These are the kinds of rules that often appear in production systems and are exactly the kind of logic interviewers look for in backend candidates.

---

## API highlights

### Employee endpoints
- `POST /employees` — create a new employee
- `GET /employees` — list employees with department filtering and pagination

### Attendance endpoints
- `POST /attendance/punch-in` — record employee check-in
- `POST /attendance/punch-out` — record employee check-out
- `GET /attendance` — retrieve attendance records with filters and pagination
- `PATCH /attendance/{emp_code}/{date}` — regularize attendance with admin reason and audit trail

### Analytics endpoints
- `GET /analytics/employees/{emp_code}/monthly` — monthly attendance summary for one employee
- `GET /analytics/departments/summary` — department-wise summary metrics
- `GET /analytics/leaderboard/late` — employee late-minute ranking
- `GET /analytics/departments/{department}/trend` — attendance trend with moving average

### Admin diagnostics
- `GET /admin/explain/{endpoint}` — inspect MongoDB execution stats and query performance

---

## MongoDB optimization details

A strong backend implementation is not just about writing endpoints; it is about making those endpoints fast at scale.

This project includes:

- unique indexes on employee and attendance keys
- compound indexes for date/status and department/joined filters
- aggregation pipelines that calculate summaries in the database instead of Python loops
- efficient lookup patterns for department-level and monthly analytics
- query explain support to inspect execution performance and confirm index usage

This demonstrates practical understanding of database optimization, not just CRUD correctness.

---

## Project structure

```text
candidate_kit/
├── app/
│   └── main.py
├── sample_data/
├── .env.example
├── README.md
├── REQUIREMENTS.txt
├── sample_seed.py
├── openapi.yaml
├── DATA_MODEL.md
├── REVIEW.md
├── DECISIONS.md
└── PROBLEM_STATEMENT.docx
```

---

## Local setup

### Prerequisites
- Python 3.9+
- MongoDB 6.0+ or MongoDB Atlas

### Install

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Configure environment

Create a `.env` file in the project root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=hrone_attendance
```

If using MongoDB Atlas, provide your Atlas connection string instead.

### Run the app

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### API docs

Open the interactive Swagger docs here:

```text
http://127.0.0.1:8000/docs
```

---

## What makes this a strong interview project

This repository is more than a simple CRUD app. It demonstrates:

- good understanding of backend API design
- real-world validation practices
- use of MongoDB aggregation for analytics
- awareness of performance bottlenecks and indexing
- clear handling of business rules and edge cases
- attention to maintainability and operational debugging

A candidate who can build and explain a project like this is often seen as someone who can think beyond basic implementation and reason about software as a scalable system.

---

## Summary

This project reflects strong backend engineering fundamentals: correctness, performance, observability, and operational thinking. It is specifically well-suited for demonstrating capability in FastAPI, MongoDB, validation, and analytics-driven backend development.

Built to solve a realistic workforce operations problem, this API is a strong representation of how a professional engineer would design and optimize a production-ready service.

---

## Author

Rushikesh Petkar

