"""Generate the synthetic operational dataset and persist it to SQLite.

Three tables, ~7,000 rows total: safety_incidents, worker_vitals,
operational_metrics. Distributions are hand-tuned to look like a real
industrial-safety operation (skewed severity, activity-correlated vitals,
incident-correlated productivity) rather than uniform random noise.
"""
import os
import random
import sqlite3
from datetime import datetime, timedelta

from faker import Faker

random.seed(42)
fake = Faker()
Faker.seed(42)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "operations.db")

N_SITES = 6
N_WORKERS = 120
N_DAYS = 180
END_DATE = datetime(2026, 7, 5)
START_DATE = END_DATE - timedelta(days=N_DAYS - 1)

SITE_KINDS = ["Refinery", "Warehouse", "Terminal", "Plant", "Yard", "Depot"]
INCIDENT_TYPES = [
    ("slip_trip_fall", 0.28),
    ("equipment_malfunction", 0.20),
    ("struck_by_object", 0.15),
    ("chemical_exposure", 0.10),
    ("fall_from_height", 0.09),
    ("electrical", 0.08),
    ("heat_stress", 0.06),
    ("other", 0.04),
]
SEVERITIES = [("low", 0.55), ("medium", 0.30), ("high", 0.12), ("critical", 0.03)]
ACTIVITY_LEVELS = [("resting", 0.25), ("light", 0.35), ("moderate", 0.28), ("vigorous", 0.12)]

INCIDENT_DESCRIPTIONS = {
    "slip_trip_fall": "Worker slipped on {surface} near {area}.",
    "equipment_malfunction": "{equipment} malfunctioned during routine operation in {area}.",
    "struck_by_object": "Worker struck by {object} while working near {area}.",
    "chemical_exposure": "Brief exposure to {chemical} during handling in {area}.",
    "fall_from_height": "Fall from {height} while accessing {area}.",
    "electrical": "Electrical fault involving {equipment} in {area}.",
    "heat_stress": "Heat stress reported after extended activity in {area}.",
    "other": "Incident reported in {area}, details logged by site supervisor.",
}
FILL = {
    "surface": ["wet flooring", "an oil spill", "loose gravel", "icy pavement", "a metal grating"],
    "area": ["the loading dock", "bay 3", "the north corridor", "the tank farm", "the maintenance shed", "the control room", "the yard"],
    "equipment": ["a conveyor belt", "a forklift", "a pressure valve", "a hydraulic lift", "a generator"],
    "object": ["a falling pallet", "a swinging beam", "a dropped tool", "a moving vehicle"],
    "chemical": ["a solvent", "ammonia vapor", "a cleaning agent", "diesel fumes"],
    "height": ["a ladder", "a scaffold", "an elevated platform", "a rooftop access point"],
}


def weighted_choice(pairs):
    options, weights = zip(*pairs)
    return random.choices(options, weights=weights, k=1)[0]


def make_sites():
    sites = []
    for i in range(N_SITES):
        kind = SITE_KINDS[i % len(SITE_KINDS)]
        city = fake.city()
        sites.append({"site_id": f"SITE-{i+1:02d}", "site_name": f"{city} {kind}", "city": city})
    return sites


def make_workers(sites):
    workers = []
    for i in range(N_WORKERS):
        site = random.choice(sites)
        workers.append({
            "worker_id": f"W-{i+1:04d}",
            "site_id": site["site_id"],
            "location": site["site_name"],
        })
    return workers


def random_datetime_in_range(start, end):
    delta = end - start
    seconds = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=seconds)


def gen_safety_incidents(workers):
    rows = []
    n = int(N_DAYS * N_SITES * 0.7)  # ~roughly one incident per site every ~1.4 days
    for i in range(n):
        worker = random.choice(workers)
        incident_type = weighted_choice(INCIDENT_TYPES)
        severity = weighted_choice(SEVERITIES)
        dt = random_datetime_in_range(START_DATE, END_DATE)
        days_old = (END_DATE - dt).days
        if days_old > 30:
            resolution_status = weighted_choice([("resolved", 0.75), ("closed", 0.23), ("in_progress", 0.02)])
        elif days_old > 7:
            resolution_status = weighted_choice([("resolved", 0.5), ("in_progress", 0.35), ("closed", 0.1), ("open", 0.05)])
        else:
            resolution_status = weighted_choice([("open", 0.4), ("in_progress", 0.45), ("resolved", 0.15)])
        template = INCIDENT_DESCRIPTIONS[incident_type]
        desc = template.format(**{k: random.choice(v) for k, v in FILL.items() if "{" + k + "}" in template})
        rows.append({
            "incident_id": f"INC-{i+1:05d}",
            "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "location": worker["location"],
            "site_id": worker["site_id"],
            "severity": severity,
            "worker_id": worker["worker_id"],
            "incident_type": incident_type,
            "resolution_status": resolution_status,
            "description": desc,
        })
    return rows


def gen_worker_vitals(workers):
    rows = []
    vid = 1
    for worker in workers:
        n_readings = random.randint(25, 60)
        for _ in range(n_readings):
            dt = random_datetime_in_range(START_DATE, END_DATE)
            activity = weighted_choice(ACTIVITY_LEVELS)
            hr_base = {"resting": 68, "light": 82, "moderate": 100, "vigorous": 130}[activity]
            heart_rate = max(45, min(190, int(random.gauss(hr_base, 9))))
            temp_base = 36.8
            if activity == "vigorous" and random.random() < 0.15:
                temp_base += random.uniform(0.5, 1.2)  # heat-stress outlier
            body_temp = round(random.gauss(temp_base, 0.25), 1)
            rows.append({
                "vital_id": f"V-{vid:06d}",
                "worker_id": worker["worker_id"],
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "heart_rate": heart_rate,
                "body_temp": body_temp,
                "activity_level": activity,
                "location": worker["location"],
            })
            vid += 1
    return rows


def gen_operational_metrics(sites, incidents_by_site_date):
    rows = []
    for site in sites:
        for d in range(N_DAYS):
            date = (START_DATE + timedelta(days=d)).strftime("%Y-%m-%d")
            key = (site["site_id"], date)
            incidents_reported = incidents_by_site_date.get(key, 0)
            near_misses = max(0, int(random.gauss(2 + incidents_reported, 1.5)))
            hours_worked = round(random.gauss(9.5, 1.2), 1)
            productivity_base = 0.88 - 0.03 * incidents_reported - 0.01 * near_misses
            productivity_index = round(max(0.3, min(1.0, random.gauss(productivity_base, 0.05))), 3)
            rows.append({
                "site_id": site["site_id"],
                "date": date,
                "hours_worked": max(0, hours_worked),
                "incidents_reported": incidents_reported,
                "near_misses": near_misses,
                "productivity_index": productivity_index,
            })
    return rows


def build():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    sites = make_sites()
    workers = make_workers(sites)
    incidents = gen_safety_incidents(workers)

    incidents_by_site_date = {}
    for inc in incidents:
        date = inc["date"][:10]
        key = (inc["site_id"], date)
        incidents_by_site_date[key] = incidents_by_site_date.get(key, 0) + 1

    vitals = gen_worker_vitals(workers)
    metrics = gen_operational_metrics(sites, incidents_by_site_date)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE safety_incidents (
            incident_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            location TEXT NOT NULL,
            site_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            incident_type TEXT NOT NULL,
            resolution_status TEXT NOT NULL,
            description TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE worker_vitals (
            vital_id TEXT PRIMARY KEY,
            worker_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            heart_rate INTEGER NOT NULL,
            body_temp REAL NOT NULL,
            activity_level TEXT NOT NULL,
            location TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE operational_metrics (
            site_id TEXT NOT NULL,
            date TEXT NOT NULL,
            hours_worked REAL NOT NULL,
            incidents_reported INTEGER NOT NULL,
            near_misses INTEGER NOT NULL,
            productivity_index REAL NOT NULL,
            PRIMARY KEY (site_id, date)
        )
    """)

    cur.executemany(
        "INSERT INTO safety_incidents VALUES (:incident_id,:date,:location,:site_id,:severity,:worker_id,:incident_type,:resolution_status,:description)",
        incidents,
    )
    cur.executemany(
        "INSERT INTO worker_vitals VALUES (:vital_id,:worker_id,:timestamp,:heart_rate,:body_temp,:activity_level,:location)",
        vitals,
    )
    cur.executemany(
        "INSERT INTO operational_metrics VALUES (:site_id,:date,:hours_worked,:incidents_reported,:near_misses,:productivity_index)",
        metrics,
    )

    cur.execute("CREATE INDEX idx_incidents_date ON safety_incidents(date)")
    cur.execute("CREATE INDEX idx_incidents_worker ON safety_incidents(worker_id)")
    cur.execute("CREATE INDEX idx_vitals_worker ON worker_vitals(worker_id)")
    cur.execute("CREATE INDEX idx_vitals_timestamp ON worker_vitals(timestamp)")
    cur.execute("CREATE INDEX idx_metrics_date ON operational_metrics(date)")

    conn.commit()
    total = len(incidents) + len(vitals) + len(metrics)
    print(f"safety_incidents: {len(incidents)} rows")
    print(f"worker_vitals: {len(vitals)} rows")
    print(f"operational_metrics: {len(metrics)} rows")
    print(f"total: {total} rows -> {os.path.abspath(DB_PATH)}")
    conn.close()


if __name__ == "__main__":
    build()
