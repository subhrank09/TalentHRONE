from bson import json_util
"""HROne attendance API assignment implementation.



Configuration: MONGO_URI and MONGO_DB environment variables.

Run: uvicorn app.main:app --port 8000

"""

import json

import os

import re

import calendar

from datetime import date, datetime, time, timedelta, timezone

from decimal import Decimal, ROUND_HALF_UP

from typing import Optional, Literal, Any



from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Query, Path as ApiPath

from pydantic import BaseModel, Field, ConfigDict, field_validator

from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument

from pymongo.errors import DuplicateKeyError, PyMongoError





load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=3000)

db = client[MONGO_DB]

employees = db["employees"]

logs = db["attendance_logs"]



UTC = timezone.utc

IST = timezone(timedelta(hours=5, minutes=30))

PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}

STATUSES = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}

EPOCH_MIN, EPOCH_MAX = 100_000_000_000, 4_102_444_800_000



app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")





# ---------- shared validation, time and serialization helpers ----------

def now_utc() -> datetime:

    return datetime.now(UTC).replace(microsecond=0)



def as_utc(dt: datetime) -> datetime:

    if dt.tzinfo is None:

        return dt.replace(tzinfo=UTC)

    return dt.astimezone(UTC)



def epoch_to_dt(value: Optional[int]) -> Optional[datetime]:

    if value is None:

        return None

    if type(value) is not int or not EPOCH_MIN <= value <= EPOCH_MAX:

        raise HTTPException(422, "timestamp must be an integer epoch-millisecond value in range")

    return datetime.fromtimestamp(value // 1000, UTC)



def dt_to_epoch(value: Optional[datetime]) -> Optional[int]:

    if value is None:

        return None

    value = as_utc(value).replace(microsecond=0)

    return int(value.timestamp()) * 1000



def clean_doc(doc: dict) -> dict:

    doc = dict(doc)

    doc.pop("_id", None)

    if "created_at" in doc:

        doc["created_at"] = dt_to_epoch(doc["created_at"])

    for key in ("punch_in", "punch_out"):

        if key in doc:

            doc[key] = dt_to_epoch(doc[key])

    doc["history"] = doc.get("history") or []

    doc["half_day"] = bool(doc.get("half_day", False))

    for h in doc["history"]:

        h["at"] = dt_to_epoch(h.get("at"))

        for change in h.get("changes", {}).values():

            for side in ("from", "to"):

                if isinstance(change.get(side), datetime):

                    change[side] = dt_to_epoch(change[side])

    return doc



def parse_date(s: str, field: str = "date") -> date:

    try:

        d = date.fromisoformat(s)

        if d.isoformat() != s:

            raise ValueError

        return d

    except (ValueError, TypeError):

        raise HTTPException(422, f"{field} must be YYYY-MM-DD")



def parse_month(s: str) -> tuple[date, date]:

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", s or ""):

        raise HTTPException(422, "month must be YYYY-MM")

    year, month = map(int, s.split("-"))

    first = date(year, month, 1)

    last = date(year, month, calendar.monthrange(year, month)[1])

    return first, last



def validate_page(page: int, page_size: int):

    if page < 1 or page_size < 1 or page_size > 100:

        raise HTTPException(422, "page must be >= 1 and page_size must be between 1 and 100")



def round_half_up(value: float, places: int = 2) -> float:

    quantum = Decimal("1").scaleb(-places)

    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))



def local_instant(dt: datetime) -> datetime:

    return as_utc(dt).astimezone(IST)



def attendance_date(punch_in: datetime, emp: dict) -> str:

    local = local_instant(punch_in)

    shift_end = time.fromisoformat(emp["shift_end"])

    shift_start = time.fromisoformat(emp["shift_start"])

    d = local.date()

    if shift_end <= shift_start and local.time().replace(tzinfo=None) < shift_end:

        d -= timedelta(days=1)

    return d.isoformat()



def shift_start_dt(att_date: str, emp: dict) -> datetime:

    d = parse_date(att_date)

    h, m = map(int, emp["shift_start"].split(":"))

    return datetime.combine(d, time(h, m), IST).astimezone(UTC)



def shift_end_dt(att_date: str, emp: dict) -> datetime:

    d = parse_date(att_date)

    start = time.fromisoformat(emp["shift_start"])

    end = time.fromisoformat(emp["shift_end"])

    if end <= start:

        d += timedelta(days=1)

    h, m = map(int, emp["shift_end"].split(":"))

    return datetime.combine(d, time(h, m), IST).astimezone(UTC)



def compute_late_minutes(punch_in: datetime, shift_start: str, att_date: str) -> int:

    start_local = datetime.combine(parse_date(att_date), time.fromisoformat(shift_start), IST)

    start = start_local.astimezone(UTC)

    delta = int((as_utc(punch_in) - start).total_seconds())

    return delta // 60 if delta > 600 else 0



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:

    seconds = int((as_utc(punch_out) - as_utc(punch_in)).total_seconds())

    return round_half_up(seconds / 3600, 2)



def compute_overtime(punch_out: datetime, shift_end: str, att_date: str, emp: dict) -> int:

    end = shift_end_dt(att_date, emp)

    mins = int((as_utc(punch_out) - end).total_seconds()) // 60

    return mins if mins >= 30 else 0



def recompute_fields(doc: dict, emp: dict) -> dict:

    pin, pout = doc.get("punch_in"), doc.get("punch_out")

    status = doc.get("status")

    if status not in PRESENCE:

        doc.update(punch_in=None, punch_out=None, work_hours=None, late_minutes=0,

                   overtime_minutes=0, half_day=False)

        return doc

    if pin is None:

        raise HTTPException(422, "presence status requires punch_in")

    pin = as_utc(pin).replace(microsecond=0)

    doc["punch_in"] = pin

    doc["late_minutes"] = compute_late_minutes(pin, emp["shift_start"], doc["date"])

    if pout is None:

        doc.update(work_hours=None, overtime_minutes=0, half_day=False)

    else:

        pout = as_utc(pout).replace(microsecond=0)

        seconds = (pout - pin).total_seconds()

        if seconds <= 0 or seconds > 24 * 3600:

            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

        doc["punch_out"] = pout

        hours = compute_work_hours(pin, pout)

        doc["work_hours"] = hours

        doc["overtime_minutes"] = compute_overtime(pout, emp["shift_end"], doc["date"], emp)

        doc["half_day"] = hours < 4.50

    return doc



def create_indexes():

    employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")

    employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="department_employee")

    employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")

    logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")

    logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")

    logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)], name="attendance_employee_date_status")

    logs.create_index([("date", ASCENDING), ("status", ASCENDING), ("late_minutes", DESCENDING)], name="attendance_date_status_late")



@app.on_event("startup")

def startup():

    create_indexes()



# ---------- request models ----------

class EmployeeCreate(BaseModel):

    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")

    name: str = Field(min_length=1, max_length=100)

    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\\.[^@\s]+$")

    department: str = Field(min_length=1, max_length=50)

    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    joined_on: str

    @field_validator("joined_on")

    @classmethod

    def valid_joined(cls, v):

        parse_date(v, "joined_on")

        return v

    @field_validator("shift_end")

    @classmethod

    def shift_differs(cls, v, info):

        start = info.data.get("shift_start", "09:30")

        if v == start:

            raise ValueError("shift_start must differ from shift_end")

        return v



class PunchInRequest(BaseModel):

    model_config = ConfigDict(extra="ignore")

    emp_code: str

    punched_at: Optional[int] = None

    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")

    @classmethod

    def valid_ts(cls, v):

        if v is not None and (type(v) is not int or not EPOCH_MIN <= v <= EPOCH_MAX):

            raise ValueError("punched_at must be epoch milliseconds")

        return v



class PunchOutRequest(BaseModel):

    model_config = ConfigDict(extra="ignore")

    emp_code: str

    punched_at: Optional[int] = None

    @field_validator("punched_at")

    @classmethod

    def valid_ts(cls, v):

        if v is not None and (type(v) is not int or not EPOCH_MIN <= v <= EPOCH_MAX):

            raise ValueError("punched_at must be epoch milliseconds")

        return v



class RegularizeRequest(BaseModel):

    model_config = ConfigDict(extra="ignore")

    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None

    punch_in: Optional[int] = None

    punch_out: Optional[int] = None

    reason: str = Field(min_length=5, max_length=200)

    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")

    @classmethod

    def valid_ts(cls, v):

        if v is not None and (type(v) is not int or not EPOCH_MIN <= v <= EPOCH_MAX):

            raise ValueError("timestamps must be epoch milliseconds")

        return v



# ---------- basic endpoints ----------

@app.get("/health")

def health():

    try:

        client.admin.command("ping")

        return {"status": "ok"}

    except PyMongoError:

        raise HTTPException(503, "MongoDB unavailable")



@app.post("/employees", status_code=201)

def create_employee(body: EmployeeCreate):

    doc = body.model_dump()

    doc["created_at"] = now_utc()

    try:

        employees.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(409, "emp_code already exists")

    return clean_doc(doc)



@app.get("/employees")

def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1),

                   page_size: int = Query(20, ge=1, le=100)):

    q = {"department": department} if department is not None else {}

    total = employees.count_documents(q)

    items = list(employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING)

                 .skip((page - 1) * page_size).limit(page_size))

    return {"items": [clean_doc(x) for x in items], "total": total, "page": page, "page_size": page_size}



@app.post("/attendance/punch-in", status_code=201)

def punch_in(body: PunchInRequest):

    emp = employees.find_one({"emp_code": body.emp_code})

    if not emp:

        raise HTTPException(404, "unknown employee")

    ts = epoch_to_dt(body.punched_at) if body.punched_at is not None else now_utc()

    ts = ts.replace(microsecond=0)

    att_date = attendance_date(ts, emp)

    doc = {"emp_code": body.emp_code, "date": att_date, "status": body.status,

           "punch_in": ts, "punch_out": None, "work_hours": None,

           "late_minutes": compute_late_minutes(ts, emp["shift_start"], att_date),

           "overtime_minutes": 0, "half_day": False, "history": []}

    try:

        logs.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(409, "already punched in for this date")

    return clean_doc(doc)



@app.get("/attendance")

def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None,

                    date_to: Optional[str] = None, status: Optional[str] = None,

                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):

    if date_from: parse_date(date_from, "date_from")

    if date_to: parse_date(date_to, "date_to")

    if date_from and date_to and date_from > date_to:

        raise HTTPException(422, "date_from must be <= date_to")

    if status and status not in STATUSES: raise HTTPException(422, "invalid status")

    q: dict[str, Any] = {}

    if emp_code is not None: q["emp_code"] = emp_code

    if date_from or date_to:

        q["date"] = {}

        if date_from: q["date"]["$gte"] = date_from

        if date_to: q["date"]["$lte"] = date_to

    if status: q["status"] = status

    total = logs.count_documents(q)

    docs = list(logs.find(q, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)])

                .skip((page - 1) * page_size).limit(page_size))

    return {"items": [clean_doc(x) for x in docs], "total": total, "page": page, "page_size": page_size}



@app.post("/attendance/punch-out")

def punch_out(body: PunchOutRequest):

    emp = employees.find_one({"emp_code": body.emp_code})

    if not emp: raise HTTPException(404, "unknown employee")

    ts = epoch_to_dt(body.punched_at) if body.punched_at is not None else now_utc()

    ts = ts.replace(microsecond=0)

    # Most recent open record whose punch-in is at or before the requested instant.

    target = logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": ts},

                            "punch_out": None, "status": {"$in": list(PRESENCE)}},

                           sort=[("punch_in", DESCENDING)])

    if target is None:

        already = logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": ts},

                                 "status": {"$in": list(PRESENCE)}, "punch_out": {"$ne": None}},

                                sort=[("punch_in", DESCENDING)])

        if already: raise HTTPException(409, "record already punched out")

        raise HTTPException(404, "no punch-in found")

    seconds = (ts - target["punch_in"]).total_seconds()

    if seconds <= 0 or seconds > 24 * 3600:

        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours")

    updated = logs.find_one_and_update(

        {"emp_code": body.emp_code, "date": target["date"], "punch_out": None},

        {"$set": {"punch_out": ts}}, return_document=ReturnDocument.AFTER)

    if updated is None: raise HTTPException(409, "record already punched out")

    updated = recompute_fields(updated, emp)

    logs.update_one({"emp_code": body.emp_code, "date": target["date"], "punch_out": ts},

                    {"$set": {k: updated[k] for k in ("work_hours", "late_minutes", "overtime_minutes", "half_day")}})

    return clean_doc(updated)



@app.patch("/attendance/{emp_code}/{date}")

def regularize(emp_code: str, date: str, body: RegularizeRequest):

    parse_date(date)

    emp = employees.find_one({"emp_code": emp_code})

    if not emp: raise HTTPException(404, "unknown employee")

    old = logs.find_one({"emp_code": emp_code, "date": date})

    if not old: raise HTTPException(404, "attendance record not found")

    old = {**old, "history": old.get("history") or [], "half_day": old.get("half_day", False),

           "late_minutes": old.get("late_minutes", 0), "overtime_minutes": old.get("overtime_minutes", 0)}

    new = dict(old)

    fields_set = body.model_fields_set

    if "status" in fields_set and body.status is not None: new["status"] = body.status

    if "punch_in" in fields_set: new["punch_in"] = epoch_to_dt(body.punch_in)

    if "punch_out" in fields_set: new["punch_out"] = epoch_to_dt(body.punch_out)

    if new["status"] in {"ABSENT", "LEAVE"}:

        if ("punch_in" in fields_set and body.punch_in is not None) or ("punch_out" in fields_set and body.punch_out is not None):

            raise HTTPException(422, "ABSENT/LEAVE cannot have punch times")

        new["punch_in"] = new["punch_out"] = None

    else:

        if new.get("punch_in") is None: raise HTTPException(422, "presence status requires punch_in")

        if attendance_date(new["punch_in"], emp) != date:

            raise HTTPException(422, "punch_in must fall on the attendance date")

    new = recompute_fields(new, emp)

    changed_fields = ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")

    changes = {}

    for key in changed_fields:

        before, after = old.get(key), new.get(key)

        if isinstance(before, datetime): before = as_utc(before).replace(microsecond=0)

        if isinstance(after, datetime): after = as_utc(after).replace(microsecond=0)

        if before != after:

            changes[key] = {"from": before, "to": after}

    if not changes: raise HTTPException(422, "request does not change the record")

    entry = {"at": now_utc(), "by": body.regularized_by, "reason": body.reason, "changes": changes}

    # Compare-and-swap prevents silent lost updates; a loser can retry and re-read history.

    history_guard = {"$or": [

        {"history": old.get("history", [])},

        {"history": {"$exists": False}} if "history" not in old else {"_id": {"$exists": False}}

    ]}

    # For legacy documents without history, match the missing field explicitly.

    if "history" in old:

        history_guard = {"history": old["history"]}

    else:

        history_guard = {"history": {"$exists": False}}

    result = logs.update_one({"_id": old["_id"], **history_guard},

        {"$set": {k: new.get(k) for k in changed_fields}, "$push": {"history": entry}})

    if result.modified_count != 1: raise HTTPException(409, "record changed concurrently; retry")

    fresh = logs.find_one({"_id": old["_id"]})

    return clean_doc(fresh)



# ---------- aggregation builders ----------

def month_bounds(month: str):

    first, last = parse_month(month)

    start = first.isoformat()

    end = (last + timedelta(days=1)).isoformat()

    return first, last, start, end



def weekday_count(start: date, end: date) -> int:

    count = 0

    cur = start

    while cur <= end:

        if cur.weekday() < 5: count += 1

        cur += timedelta(days=1)

    return count



def present_expr():

    return {"$in": ["$status", list(PRESENCE)]}



def employee_monthly_pipeline(emp_code: str, month: str):

    first, last, start, end = month_bounds(month)

    return [

        {"$match": {"emp_code": emp_code, "date": {"$gte": start, "$lt": end}}},

        {"$group": {"_id": None,

            "present_days": {"$sum": {"$cond": [{"$and": [present_expr(), {"$lt": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 7]}, {"$gt": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 1]}]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},

            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},

            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},

            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},

            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}

        }}

    ]



def department_summary_pipeline(month: str, department: Optional[str]):

    first, last, start, end = month_bounds(month)

    match = {"joined_on": {"$lte": last.isoformat()}}

    if department is not None: match["department"] = department

    return [

        {"$match": match},

        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"},

            "pipeline": [{"$match": {"$expr": {"$and": [

                {"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", start]}, {"$lt": ["$date", end]}

            ]}}}], "as": "month_logs"}},

        {"$unwind": {"path": "$month_logs", "preserveNullAndEmptyArrays": True}},

        {"$group": {"_id": "$department",

            "headcount_codes": {"$addToSet": "$emp_code"},

            "present_days": {"$sum": {"$cond": [{"$and": [{"$ne": ["$month_logs", None]}, {"$in": ["$month_logs.status", list(PRESENCE)]},

                {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$month_logs.date"}}}, 2]},

                {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$month_logs.date"}}}, 6]}]},

                {"$cond": [{"$eq": ["$month_logs.half_day", True]}, 0.5, 1]}, 0]}},

            "work_hours_sum": {"$sum": {"$cond": [{"$and": [{"$in": ["$month_logs.status", list(PRESENCE)]}, {"$ne": ["$month_logs.work_hours", None]}]}, "$month_logs.work_hours", 0]}},

            "work_hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$month_logs.status", list(PRESENCE)]}, {"$ne": ["$month_logs.work_hours", None]}]}, 1, 0]}},

            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$month_logs.late_minutes", 0]}, 0]}, 1, 0]}},

            "total_late_minutes": {"$sum": {"$ifNull": ["$month_logs.late_minutes", 0]}},

            "leave_count": {"$sum": {"$cond": [{"$eq": ["$month_logs.status", "LEAVE"]}, 1, 0]}},

            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$month_logs.status", "ON_DUTY"]}, 1, 0]}}

        }},

        {"$project": {"_id": 0, "department": "$_id", "headcount": {"$size": "$headcount_codes"},

            "present_days": 1, "avg_work_hours": {"$cond": [{"$gt": ["$work_hours_count", 0]}, {"$round": [{"$divide": ["$work_hours_sum", "$work_hours_count"]}, 2]}, None]},

            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}},

        {"$match": {"headcount": {"$gt": 0}}}, {"$sort": {"department": 1}}

    ]



def leaderboard_pipeline(month: str, department: Optional[str], limit: int):

    first, last, start, end = month_bounds(month)

    employee_match = {"$expr": {"$eq": ["$emp_code", "$$code"]}}

    lookup_pipeline = [{"$match": {"$expr": {"$and": [employee_match, {"$gte": ["$date", start]}, {"$lt": ["$date", end]}]}}},

        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},

                    "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}}]

    pipeline = [{"$match": ({"department": department} if department is not None else {})},

        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": lookup_pipeline, "as": "totals"}},

        {"$unwind": "$totals"}, {"$match": {"totals.total_late_minutes": {"$gt": 0}}},

        {"$set": {"total_late_minutes": "$totals.total_late_minutes", "late_count": "$totals.late_count"}},

        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1},

            "output": {"rank": {"$rank": {}}}}},

        {"$match": {"rank": {"$lte": limit}}},

        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},

        {"$project": {"_id": 0, "rank": 1, "emp_code": 1, "name": 1, "department": 1, "total_late_minutes": 1, "late_count": 1}}]

    return pipeline



@app.get("/analytics/employees/{emp_code}/monthly")

def employee_monthly(emp_code: str, month: str):

    emp = employees.find_one({"emp_code": emp_code}, {"_id": 1, "joined_on": 1})

    if not emp: raise HTTPException(404, "unknown employee")

    first, last, start, end = month_bounds(month)

    start_work = max(first, parse_date(emp["joined_on"]))

    working_days = weekday_count(start_work, last) if start_work <= last else 0

    agg = list(logs.aggregate(employee_monthly_pipeline(emp_code, month)))

    x = agg[0] if agg else {}

    pct = round_half_up(x.get("present_days", 0) / working_days * 100, 2) if working_days else None

    return {"emp_code": emp_code, "month": month, "working_days": working_days,

            "present_days": round_half_up(x.get("present_days", 0), 2), "leave_days": x.get("leave_days", 0),

            "late_count": x.get("late_count", 0), "total_late_minutes": x.get("total_late_minutes", 0),

            "total_overtime_minutes": x.get("total_overtime_minutes", 0), "attendance_pct": pct}



@app.get("/analytics/departments/summary")

def department_summary(month: str, department: Optional[str] = None):

    month_bounds(month)

    items = list(employees.aggregate(department_summary_pipeline(month, department), allowDiskUse=True))

    for item in items:

        item["present_days"] = round_half_up(item.get("present_days", 0), 2)

        if item.get("avg_work_hours") is not None: item["avg_work_hours"] = round_half_up(item["avg_work_hours"], 2)

    return {"month": month, "items": items}



@app.get("/analytics/leaderboard/late")

def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):

    month_bounds(month)

    return {"month": month, "items": list(employees.aggregate(leaderboard_pipeline(month, department, limit), allowDiskUse=True))}



@app.get("/analytics/departments/{department}/trend")

def department_trend(department: str, from_date: str = Query(alias="from"), to_date: str = Query(alias="to")):

    start = parse_date(from_date, "from")

    end = parse_date(to_date, "to")

    if end < start: raise HTTPException(422, "to must be >= from")

    days = (end - start).days + 1

    if days > 92: raise HTTPException(422, "range must not exceed 92 days")

    if not employees.find_one({"department": department}): raise HTTPException(404, "unknown department")

    # Gap-fill and moving-average work is performed inside MongoDB. Employee lookup includes no-log staff.

    pipeline = [

        {"$match": {"department": department}},

        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"},

            "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]},

                {"$gte": ["$date", from_date]}, {"$lte": ["$date", to_date]}]}}},

                {"$project": {"_id": 0, "date": 1, "status": 1, "late_minutes": 1, "half_day": 1}}], "as": "logs"}},

        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},

        {"$group": {"_id": {"$ifNull": ["$logs.date", None]},

            "codes": {"$addToSet": "$emp_code"},

            "headcount": {"$sum": {"$cond": [{"$lte": ["$joined_on", {"$ifNull": ["$logs.date", to_date]}]}, 1, 0]}},

            "present_count": {"$sum": {"$cond": [{"$in": ["$logs.status", list(PRESENCE)]},

                {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]}, 0]}},

            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}}}},

        {"$match": {"_id": {"$ne": None}}},

        {"$project": {"_id": 0, "date": "$_id", "headcount": 1, "present_count": 1, "late_count": 1}},

        {"$sort": {"date": 1}},

        {"$set": {"_id": {"$dateFromString": {"dateString": "$date"}}}},

        {"$densify": {"field": "_id", "range": {"step": 1, "unit": "day", "bounds": [

            {"$dateFromString": {"dateString": from_date}}, {"$dateFromString": {"dateString": to_date}}

        ]}}},

        {"$set": {"date": {"$dateToString": {"date": "$_id", "format": "%Y-%m-%d"}},

            "is_working_day": {"$and": [{"$gte": [{"$dayOfWeek": "$_id"}, 2]}, {"$lte": [{"$dayOfWeek": "$_id"}, 6]}]}}},

        {"$set": {"headcount": {"$ifNull": ["$headcount", 0]}, "present_count": {"$ifNull": ["$present_count", 0]},

            "late_count": {"$ifNull": ["$late_count", 0]}}},

        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},

            {"$round": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 100]}, 4]}, None]}}},

        {"$setWindowFields": {"sortBy": {"_id": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate",

            "window": {"documents": [-6, "current"]}}}}},

        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1,

            "late_count": 1, "attendance_rate": 1, "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}}},

        {"$sort": {"date": 1}}

    ]

    # Use a database-side calendar scaffold to guarantee dates with no logs.

    # $densify does not create rows when the pipeline has zero input, so union a generated calendar.

    calendar_pipeline = [

        {"$documents": [{"date": (start + timedelta(days=i)).isoformat()} for i in range(days)]},

        {"$set": {"_id": {"$dateFromString": {"dateString": "$date"}}}},

        {"$lookup": {"from": "employees", "let": {"day": "$date"}, "pipeline": [

            {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}},

            {"$count": "n"}], "as": "hc"}},

        {"$lookup": {"from": "attendance_logs", "let": {"day": "$date"}, "pipeline": [

            {"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},

            {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "e"}},

            {"$unwind": "$e"}, {"$match": {"e.department": department}},

            {"$group": {"_id": None,

                "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]},

                    {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},

                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}}], "as": "agg"}},

        {"$set": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]},

            "present_count": {"$ifNull": [{"$arrayElemAt": ["$agg.present_count", 0]}, 0]},

            "late_count": {"$ifNull": [{"$arrayElemAt": ["$agg.late_count", 0]}, 0]},

            "is_working_day": {"$and": [{"$gte": [{"$dayOfWeek": "$_id"}, 2]}, {"$lte": [{"$dayOfWeek": "$_id"}, 6]}]}}},

        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},

            {"$round": [{"$multiply": [{"$divide": ["$present_count", "$headcount"]}, 100]}, 4]}, None]}}},

        {"$setWindowFields": {"sortBy": {"_id": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate",

            "window": {"documents": [-6, "current"]}}}}},

        {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1,

            "late_count": 1, "attendance_rate": 1, "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}}},

        {"$sort": {"date": 1}}

    ]

    items = list(db.aggregate(calendar_pipeline, allowDiskUse=True))

    return {"department": department, "items": items}



# ---------- explain endpoint ----------



@app.get("/admin/explain/{endpoint}")

def explain_endpoint(

    endpoint: str,

    emp_code: Optional[str] = None,

    month: Optional[str] = None,

    department: Optional[str] = None,

    limit: int = Query(10, ge=1, le=50),

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    status: Optional[str] = None,

    page: int = Query(1, ge=1),

    page_size: int = Query(20, ge=1, le=100),

    from_date: Optional[str] = Query(None, alias="from"),

    to_date: Optional[str] = Query(None, alias="to"),

):

    allowed = {

        "attendance_list",

        "employee_monthly",

        "department_summary",

        "late_leaderboard",

        "department_trend",

    }



    if endpoint not in allowed:

        raise HTTPException(status_code=422, detail="unknown explain endpoint")



    if date_from:

        parse_date(date_from, "date_from")

    if date_to:

        parse_date(date_to, "date_to")

    if date_from and date_to and date_from > date_to:

        raise HTTPException(status_code=422, detail="date_from must be <= date_to")



    if endpoint == "attendance_list":

        q: dict[str, Any] = {}



        if emp_code:

            q["emp_code"] = emp_code



        if date_from or date_to:

            q["date"] = {}

            if date_from:

                q["date"]["$gte"] = date_from

            if date_to:

                q["date"]["$lte"] = date_to



        if status:

            q["status"] = status



        exp = logs.database.command(
            "explain",
            {
                "find": logs.name,
                "filter": q,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )
        collection = "attendance_logs"



    elif endpoint == "employee_monthly":

        if not emp_code or not month:

            raise HTTPException(

                status_code=422,

                detail="emp_code and month are required",

            )



        month_bounds(month)

        pipeline = employee_monthly_pipeline(emp_code, month)



        exp = logs.database.command(

            "explain",

            {

                "aggregate": logs.name,

                "pipeline": pipeline,

                "cursor": {},

            },

            verbosity="executionStats",

        )

        collection = "attendance_logs"



    elif endpoint == "department_summary":

        if not month:

            raise HTTPException(status_code=422, detail="month is required")



        month_bounds(month)

        pipeline = department_summary_pipeline(month, department)



        exp = employees.database.command(

            "explain",

            {

                "aggregate": employees.name,

                "pipeline": pipeline,

                "cursor": {},

                "allowDiskUse": True,

            },

            verbosity="executionStats",

        )

        collection = "employees"



    elif endpoint == "late_leaderboard":

        if not month:

            raise HTTPException(status_code=422, detail="month is required")



        month_bounds(month)

        pipeline = leaderboard_pipeline(month, department, limit)



        exp = employees.database.command(

            "explain",

            {

                "aggregate": employees.name,

                "pipeline": pipeline,

                "cursor": {},

                "allowDiskUse": True,

            },

            verbosity="executionStats",

        )

        collection = "employees"



    else:  # department_trend

        if not department or not from_date or not to_date:

            raise HTTPException(

                status_code=422,

                detail="department, from and to are required",

            )



        start = parse_date(from_date, "from")

        end = parse_date(to_date, "to")



        if start > end:

            raise HTTPException(status_code=422, detail="'from' must be <= 'to'")



        if (end - start).days > 91:

            raise HTTPException(

                status_code=422,

                detail="date range must not exceed 92 days",

            )



        # Explain the indexed attendance-date query used by the trend.

        q = {"date": {"$gte": from_date, "$lte": to_date}}



        exp = logs.database.command(
            "explain",
            {"find": logs.name, "filter": q},
            verbosity="executionStats",
        )

        collection = "attendance_logs"



    # MongoDB explain output can contain BSON Timestamp and other BSON types.

    # Convert these to JSON-safe Extended JSON before returning through FastAPI.

    safe_explain = json.loads(json_util.dumps(exp))



    return {

        "endpoint": endpoint,

        "collection": collection,

        "explain": safe_explain,

    }


