# HROne Employee Attendance & Analytics API

A REST API built with **FastAPI, Python, and MongoDB** for employee management, attendance punch-in/punch-out, manual attendance corrections with audit history, and department-level analytics.

## Requirements

- Python 3.11+
- MongoDB 6.0+
- Dependencies listed in `requirements.txt`

## Setup and Run

Create and activate a virtual environment, then install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Configure the MongoDB connection using environment variables:

```bash
export MONGO_URI="your-mongodb-connection-string"
export MONGO_DB="attendance_db"
```

Start the application from the repository root:

```bash
uvicorn app.main:app --port 8000
```

The application creates the required MongoDB indexes at startup.

## API Documentation

Once the application is running, access:

- **Health Check:** http://127.0.0.1:8000/health
- **Interactive API Documentation:** http://127.0.0.1:8000/docs
- **OpenAPI Schema:** http://127.0.0.1:8000/openapi.json

## Features

- Employee creation and listing.
- Attendance punch-in and punch-out with validation.
- Attendance listing with filters and pagination.
- Manual attendance corrections with an audit trail.
- Employee monthly attendance analytics.
- Department attendance summary.
- Late-arrival leaderboard with ranking.
- Department trend analytics with a moving average.
- MongoDB indexes and explain endpoints for query-plan inspection.

## Implementation Notes

- API timestamps use epoch milliseconds, while MongoDB stores instants as UTC datetimes.
- Attendance calendar dates and shift times follow Indian Standard Time (IST).
- Work hours, late minutes, overtime minutes, and half-day status are derived from punch times and shift rules.
- Analytics are implemented using MongoDB aggregation pipelines.
- MongoDB indexes are created automatically during application startup.

## Validation

The API was exercised locally using Swagger UI and HTTP requests, covering attendance operations, analytics endpoints, and explain-plan inspection.

For reproducibility, run the application against a MongoDB instance containing suitable employee and attendance data.

Review the following documents for additional details:

- `REVIEW.md` — implementation findings and review observations.
- `DECISIONS.md` — design decisions and trade-offs.

## Security

- Do not commit database credentials or `.env` files.
- Configure `MONGO_URI` and `MONGO_DB` through environment variables.
- Use appropriate MongoDB network access restrictions and credentials for production deployments.
