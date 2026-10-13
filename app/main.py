"""Employee Attendance & Analytics API (contract v2.0)."""
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
import json
from bson import json_util
from bson.decimal128 import Decimal128

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

project_dir = Path(__file__).resolve().parents[1]
load_dotenv(project_dir / ".env")
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
mongo_db = os.getenv("MONGO_DB", "attendance_db")
client = MongoClient(mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000)
db = client[mongo_db]
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_emp_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="ix_employee_department_joined")
    db.employees.create_index([("joined_on", ASCENDING), ("department", ASCENDING)], name="ix_employee_joined_department")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="ix_employee_department_code")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_emp_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_emp")
    db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING)], name="ix_attendance_date_status")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="ix_attendance_emp_punchin")


def fail422(message: str):
    raise HTTPException(422, message)


def date_value(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (TypeError, ValueError):
        fail422("date must be YYYY-MM-DD")


def month_bounds(month: str):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        fail422("month must be YYYY-MM")
    try:
        start = date.fromisoformat(month + "-01")
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1) - timedelta(days=1)
        return start.isoformat(), end.isoformat(), start, end
    except (ValueError, OverflowError):
        fail422("Invalid month value")


def to_dt(value: Optional[int]) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    if type(value) is not int or not 100_000_000_000 <= value <= 4_102_444_800_000:
        fail422("timestamps must be epoch milliseconds")
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)


def epoch(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def round2(n):
    return float(Decimal(str(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round4(n):
    return float(Decimal(str(n)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def shift_start_dt(day: date, value: str) -> datetime:
    h, m = map(int, value.split(":"))
    return datetime.combine(day, time(h, m), IST)


def shift_context(emp, punch_in: datetime):
    local = punch_in.astimezone(IST)
    day = local.date()
    sh, sm = map(int, emp["shift_start"].split(":"))
    eh, em = map(int, emp["shift_end"].split(":"))
    start_clock, end_clock = (sh, sm), (eh, em)
    overnight = end_clock <= start_clock
    if overnight and (local.hour, local.minute) < end_clock:
        day -= timedelta(days=1)
    start = shift_start_dt(day, emp["shift_start"])
    end_day = day + timedelta(days=1 if overnight else 0)
    end = shift_start_dt(end_day, emp["shift_end"])
    return day.isoformat(), start, end


def derived(emp, day_str, punch_in, punch_out):
    if not punch_in:
        return {"late_minutes": 0, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    d = date_value(day_str)
    _, shift_start, shift_end = shift_context(emp, punch_in)
    delay = (punch_in - shift_start).total_seconds()
    late = int(delay // 60) if delay > 600 else 0
    if punch_out is None:
        return {"late_minutes": late, "work_hours": None, "overtime_minutes": 0, "half_day": False}
    hours = round2((punch_out - punch_in).total_seconds() / 3600)
    over_secs = (punch_out - shift_end).total_seconds()
    overtime = int(over_secs // 60) if over_secs >= 1800 else 0
    return {"late_minutes": late, "work_hours": hours, "overtime_minutes": overtime, "half_day": hours < 4.50}


def out_record(doc):
    doc = dict(doc)
    doc.pop("_id", None)
    doc.setdefault("history", [])
    doc.setdefault("half_day", False)
    doc.setdefault("late_minutes", 0)
    doc.setdefault("overtime_minutes", 0)
    doc.setdefault("work_hours", None)
    doc["punch_in"] = epoch(doc.get("punch_in"))
    doc["punch_out"] = epoch(doc.get("punch_out"))
    for h in doc["history"]:
        h["at"] = epoch(h.get("at"))
        for change in h.get("changes", {}).values():
            for side in ("from", "to"):
                if isinstance(change.get(side), datetime):
                    change[side] = epoch(change[side])
    return doc


def working_days(start: date, end: date):
    n = 0
    while start <= end:
        n += start.weekday() < 5
        start += timedelta(days=1)
    return n


def page_args(page, page_size):
    if page < 1 or not 1 <= page_size <= 100:
        fail422("page must be >= 1 and page_size must be between 1 and 100")


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined(cls, v):
        date_value(v)
        return v

    @model_validator(mode="after")
    def shifts_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None and (type(v) is not int or not 100_000_000_000 <= v <= 4_102_444_800_000):
            raise ValueError("punched_at must be epoch milliseconds")
        return v

    @field_validator("status")
    @classmethod
    def valid_presence_status(cls, v):
        if v not in PRESENCE:
            raise ValueError("status must be PRESENT, WFH, or ON_DUTY")
        return v


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None and (type(v) is not int or not 100_000_000_000 <= v <= 4_102_444_800_000):
            raise ValueError("punched_at must be epoch milliseconds")
        return v


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("status")
    @classmethod
    def valid_status(cls, v):
        if v is not None and v not in STATUSES:
            raise ValueError("invalid attendance status")
        return v

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_times(cls, v):
        if v is not None and (type(v) is not int or not 100_000_000_000 <= v <= 4_102_444_800_000):
            raise ValueError("timestamps must be epoch milliseconds")
        return v

    @model_validator(mode="after")
    def reject_null_updates(self):
        for key in ("status", "punch_in", "punch_out"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} cannot be null")
        return self


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = epoch(doc["created_at"])
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {"department": department} if department is not None else {}
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page - 1) * page_size).limit(page_size))
    for item in items:
        item["created_at"] = epoch(item.get("created_at"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "employee not found")
    ts = to_dt(body.punched_at)
    day, _, _ = shift_context(emp, ts)
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status, "punch_in": ts, "punch_out": None,
           "work_hours": None, **derived(emp, day, ts, None), "history": []}
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return out_record(doc)


@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                    status: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if date_from is not None: date_value(date_from)
    if date_to is not None: date_value(date_to)
    if date_from is not None and date_to is not None and date_from > date_to:
        fail422("date_from must be on or before date_to")
    q = {}
    if emp_code is not None: q["emp_code"] = emp_code
    if status is not None:
        if status not in STATUSES: fail422("invalid status")
        q["status"] = status
    if date_from is not None or date_to is not None:
        q["date"] = {}
        if date_from is not None: q["date"]["$gte"] = date_from
        if date_to is not None: q["date"]["$lte"] = date_to
    total = db.attendance_logs.count_documents(q)
    docs = db.attendance_logs.find(q, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page - 1) * page_size).limit(page_size)
    return {"items": [out_record(d) for d in docs], "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp: raise HTTPException(404, "employee not found")
    ts = to_dt(body.punched_at)
    rec = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$lte": ts}} , sort=[("punch_in", DESCENDING)])
    if not rec: raise HTTPException(404, "no punch-in found")
    if rec.get("punch_out") is not None: raise HTTPException(409, "already punched out")
    if ts <= rec["punch_in"] or ts - rec["punch_in"] > timedelta(hours=24): fail422("punch-out must be after punch-in and within 24 hours")
    values = derived(emp, rec["date"], rec["punch_in"], ts)
    updated = db.attendance_logs.find_one_and_update({"_id": rec["_id"], "punch_out": None}, {"$set": {"punch_out": ts, **values}}, return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409, "already punched out")
    return out_record(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: str, body: RegularizeIn):
    date_value(date)
    emp = db.employees.find_one({"emp_code": emp_code})
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not emp or not rec: raise HTTPException(404, "employee or attendance record not found")
    new_status = body.status if body.status is not None else rec["status"]
    if new_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None: fail422("ABSENT/LEAVE cannot have punch times")
        new_in = new_out = None
    else:
        new_in = to_dt(body.punch_in) if body.punch_in is not None else rec.get("punch_in")
        new_out = to_dt(body.punch_out) if body.punch_out is not None else rec.get("punch_out")
        if new_in is None: fail422("presence status requires punch_in")
        expected_day, _, _ = shift_context(emp, new_in)
        if expected_day != date: fail422("punch_in must fall on the attendance date")
        if new_out is not None and (new_out <= new_in or new_out - new_in > timedelta(hours=24)): fail422("punch_out must be after punch_in and within 24 hours")
    fields = {"status": new_status, "punch_in": new_in, "punch_out": new_out}
    fields.update(derived(emp, date, new_in, new_out))
    changes = {}
    defaults = {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    for key, val in fields.items():
        old = rec.get(key, defaults.get(key))
        if old != val: changes[key] = {"from": old, "to": val}
    if not changes: fail422("request does not change the record")
    history = {"at": datetime.now(UTC), "by": body.regularized_by, "reason": body.reason, "changes": changes}
    query = {"_id": rec["_id"], "punch_out": rec.get("punch_out"),
             "$or": [{"history": rec["history"]}] if "history" in rec else [{"history": {"$exists": False}}]}
    updated = db.attendance_logs.find_one_and_update(query, {"$set": fields, "$push": {"history": history}}, return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409, "record was changed concurrently")
    return out_record(updated)


def monthly_log_match(emp_code, first, last):
    return {"emp_code": emp_code, "date": {"$gte": first, "$lte": last}}


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    first, last, start, end = month_bounds(month)
    emp = db.employees.find_one({"emp_code": emp_code}, {"_id": 0, "joined_on": 1})
    if not emp: raise HTTPException(404, "employee not found")
    first_work = max(start, date_value(emp["joined_on"]))
    working = working_days(first_work, end) if first_work <= end else 0
    pipeline = employee_monthly_pipeline(emp_code, first, last)
    agg = list(db.attendance_logs.aggregate(pipeline))
    x = agg[0] if agg else {}
    present = x.get("present_days", 0)
    return {"emp_code": emp_code, "month": month, "working_days": working, "present_days": present,
            "leave_days": x.get("leave_days", 0), "late_count": x.get("late_count", 0),
            "total_late_minutes": x.get("total_late_minutes", 0), "total_overtime_minutes": x.get("total_overtime_minutes", 0),
            "attendance_pct": round4(present / working * 100) if working else None}


def employee_monthly_pipeline(emp_code, first, last):
    return [{"$match": monthly_log_match(emp_code, first, last)},
        {"$set": {"_weekday": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}}},
        {"$group": {"_id": None,
            "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENCE)]}, {"$not": [{"$in": ["$_weekday", [1, 7]]}]}]}, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}}}]


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    first, last, _, _ = month_bounds(month)
    pipeline = department_summary_pipeline(first, last, department)
    items = list(db.employees.aggregate(pipeline))
    for x in items:
        if x["headcount"] == 0: continue
        if x["avg_work_hours"] is not None: x["avg_work_hours"] = round2(x["avg_work_hours"])
    return {"month": month, "items": [x for x in items if x["headcount"] > 0]}


def department_summary_pipeline(first, last, department=None):
    match = {"joined_on": {"$lte": last}}
    if department is not None:
        match["department"] = department
    return [{"$match": match},
        {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
            {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", first]}, {"$lte": ["$date", last]}]}}}], "as": "logs"}},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$set": {"_logs_weekday": {"$cond": [
            {"$ne": [{"$ifNull": ["$logs.date", None]}, None]},
            {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, None]}}},
        {"$group": {"_id": "$department", "employees": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$not": [{"$in": ["$_logs_weekday", [1, 7]]}]}]}, {"$cond": [{"$ifNull": ["$logs.half_day", False]}, 0.5, 1]}, 0]}},
            "hours_sum": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}]}, "$logs.work_hours", 0]}},
            "hours_count": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENCE)]}, {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
            "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}}},
        {"$project": {"_id": 0, "department": "$_id", "headcount": {"$size": "$employees"}, "present_days": 1,
            "avg_work_hours": {"$cond": [{"$gt": ["$hours_count", 0]}, {"$divide": ["$hours_sum", "$hours_count"]}, None]},
            "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}},
        {"$sort": {"department": 1}}]


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    first, last, _, _ = month_bounds(month)
    pipeline = late_leaderboard_pipeline(first, last, limit, department)
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


def late_leaderboard_pipeline(first, last, limit, department=None):
    pipeline = [{"$match": {"date": {"$gte": first, "$lte": last}, "late_minutes": {"$gt": 0}}},
        {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}, "late_count": {"$sum": 1}}}, {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"}]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline += [{"$set": {"emp_code": "$_id", "name": "$employee.name", "department": "$employee.department"}},
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}}, {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": 1, "name": 1, "department": 1, "total_late_minutes": 1, "late_count": 1}}]
    return pipeline


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, from_date: str = Query(..., alias="from"), to_date: str = Query(..., alias="to")):
    start, end = date_value(from_date), date_value(to_date)
    if end < start or (end - start).days > 91: fail422("date range must be ordered and no longer than 92 days")
    if not db.employees.find_one({"department": department}): raise HTTPException(404, "department not found")
    pipeline = department_trend_pipeline(department, from_date, to_date)
    rows = list(db.employees.aggregate(pipeline))
    for row in rows:
        for key in ("attendance_rate", "moving_avg_7d"):
            if row[key] is not None:
                row[key] = round4(row[key])
    return {"department": department, "items": rows}


def department_trend_pipeline(department, from_date, to_date):
    start, end = date_value(from_date), date_value(to_date)
    begin_dt = datetime.combine(start, time.min, UTC)
    day_count = (end - start).days + 1
    rounded_rate = {"$divide": [{"$floor": {"$add": [
        {"$multiply": [{"$divide": [{"$toDecimal": "$present_count"}, {"$toDecimal": "$headcount"}]}, Decimal128("10000")]},
        Decimal128("0.5")]}}, Decimal128("10000")]}
    return [{"$match": {"department": department}}, {"$limit": 1},
        {"$project": {"_id": 0, "days": {"$map": {"input": {"$range": [0, day_count]}, "as": "i", "in": {"$dateAdd": {"startDate": begin_dt, "unit": "day", "amount": "$$i"}}}}}},
        {"$unwind": "$days"}, {"$set": {"date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$days"}}}},
        {"$lookup": {"from": "attendance_logs", "let": {"d": "$date"}, "pipeline": [
            {"$match": {"$expr": {"$eq": ["$date", "$$d"]}}}, {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "e"}},
            {"$unwind": "$e"}, {"$match": {"e.department": department}}, {"$group": {"_id": None,
                "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENCE)]}, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}},
                "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}}], "as": "logs"}},
        {"$lookup": {"from": "employees", "let": {"d": "$date"}, "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$department", department]}, {"$lte": ["$joined_on", "$$d"]}]}}}, {"$count": "n"}], "as": "hc"}},
        {"$set": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]}, "present_count": {"$ifNull": [{"$arrayElemAt": ["$logs.present_count", 0]}, 0]}, "late_count": {"$ifNull": [{"$arrayElemAt": ["$logs.late_count", 0]}, 0]}, "is_working_day": {"$not": [{"$in": [{"$dayOfWeek": "$days"}, [1, 7]]}]}}},
        {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]}, rounded_rate, None]}}},
        {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}}}},
        {"$sort": {"date": 1}}, {"$project": {"_id": 0, "date": 1, "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": 1}}]

def explain_cmd(coll, pipeline=None, query=None, sort=None, skip=0, limit=None):
    database = coll.database
    if pipeline is not None:
        cmd = {"aggregate": coll.name, "pipeline": pipeline, "cursor": {}}
        return database.command("explain", cmd, verbosity="executionStats")
    cmd = {"find": coll.name, "filter": query or {}}
    if sort:
        cmd["sort"] = dict(sort)
    if skip > 0:
        cmd["skip"] = skip
    if limit and limit > 0:
        cmd["limit"] = limit
    return database.command("explain", cmd, verbosity="executionStats")


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str, emp_code: Optional[str] = None, month: Optional[str] = None, department: Optional[str] = None,
                     limit: int = Query(10, ge=1, le=50), date_from: Optional[str] = None, date_to: Optional[str] = None,
                     status: Optional[str] = None, from_date: Optional[str] = Query(None, alias="from"), to_date: Optional[str] = Query(None, alias="to"),
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    page_args(page, page_size)
    if endpoint == "attendance_list":
        q = {}
        if emp_code is not None: q["emp_code"] = emp_code
        if status is not None:
            if status not in STATUSES: fail422("invalid status")
            q["status"] = status
        if date_from is not None: date_value(date_from)
        if date_to is not None: date_value(date_to)
        if date_from is not None and date_to is not None and date_from > date_to:
            fail422("date_from must be on or before date_to")
        if date_from is not None or date_to is not None:
            q["date"] = {}
            if date_from is not None: q["date"]["$gte"] = date_from
            if date_to is not None: q["date"]["$lte"] = date_to
        explain = explain_cmd(db.attendance_logs, query=q, sort=[("date", -1), ("emp_code", 1)], skip=(page - 1) * page_size, limit=page_size)
        coll = "attendance_logs"
    elif endpoint == "employee_monthly" and emp_code is not None and month is not None:
        first, last, _, _ = month_bounds(month)
        explain = explain_cmd(db.attendance_logs, pipeline=employee_monthly_pipeline(emp_code, first, last))
        coll = "attendance_logs"
    elif endpoint == "department_summary" and month is not None:
        first, last, _, _ = month_bounds(month)
        explain = explain_cmd(db.employees, pipeline=department_summary_pipeline(first, last, department))
        coll = "employees"
    elif endpoint == "late_leaderboard" and month is not None:
        first, last, _, _ = month_bounds(month)
        explain = explain_cmd(db.attendance_logs, pipeline=late_leaderboard_pipeline(first, last, limit, department))
        coll = "attendance_logs"
    elif endpoint == "department_trend" and department is not None and from_date is not None and to_date is not None:
        start, end = date_value(from_date), date_value(to_date)
        if end < start or (end - start).days > 91:
            fail422("date range must be ordered and no longer than 92 days")
        explain = explain_cmd(db.employees, pipeline=department_trend_pipeline(department, from_date, to_date))
        coll = "employees"
    else:
        fail422("missing or invalid parameters for endpoint")

    safe_explain = json.loads(json_util.dumps(explain))
    return {"endpoint": endpoint, "collection": coll, "explain": safe_explain}
