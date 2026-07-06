import sqlite3
from .config import settings

SCHEMA_DESCRIPTION = """
Table safety_incidents (one row per reported safety incident):
  incident_id TEXT PRIMARY KEY
  date TEXT            -- ISO datetime, e.g. '2026-03-14 09:22:00'
  location TEXT         -- human-readable site name
  site_id TEXT          -- FK to operational_metrics.site_id, e.g. 'SITE-01'
  severity TEXT         -- one of: low, medium, high, critical
  worker_id TEXT        -- FK to worker_vitals.worker_id, e.g. 'W-0012'
  incident_type TEXT    -- one of: slip_trip_fall, equipment_malfunction, struck_by_object,
                        --   chemical_exposure, fall_from_height, electrical, heat_stress, other
  resolution_status TEXT -- one of: open, in_progress, resolved, closed
  description TEXT

Table worker_vitals (one row per sensor reading for a worker):
  vital_id TEXT PRIMARY KEY
  worker_id TEXT        -- FK to safety_incidents.worker_id
  timestamp TEXT        -- ISO datetime
  heart_rate INTEGER     -- beats per minute
  body_temp REAL         -- degrees Celsius
  activity_level TEXT    -- one of: resting, light, moderate, vigorous
  location TEXT          -- human-readable site name

Table operational_metrics (one row per site per day):
  site_id TEXT           -- e.g. 'SITE-01'
  date TEXT              -- ISO date, e.g. '2026-03-14'
  hours_worked REAL
  incidents_reported INTEGER
  near_misses INTEGER
  productivity_index REAL -- 0.0 to 1.0
  PRIMARY KEY (site_id, date)

Table workers (one row per worker; personnel directory):
  worker_id TEXT PRIMARY KEY  -- FK to safety_incidents.worker_id and worker_vitals.worker_id
  full_name TEXT
  role TEXT              -- e.g. operator, technician, supervisor, engineer
  site_id TEXT
  hire_date TEXT         -- ISO date
  national_id TEXT       -- restricted personal data
  home_address TEXT      -- restricted personal data
  phone TEXT             -- restricted personal data
  medical_conditions TEXT -- restricted personal data
  monthly_salary_aed INTEGER -- restricted personal data
""".strip()


def get_connection(readonly=True):
    if readonly:
        uri = f"file:{settings.operations_db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(settings.operations_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def estimate_rows(sql):
    """How many rows would this query return, without materializing them all?
    Wraps the query in a COUNT(*) subquery. Returns None if the wrap fails
    (some queries can't be counted this way), so callers treat unknown as
    'no size objection'."""
    wrapped = f"SELECT COUNT(*) FROM ({sql.strip().rstrip(';')})"
    try:
        conn = get_connection()
        n = conn.execute(wrapped).fetchone()[0]
        conn.close()
        return int(n)
    except sqlite3.Error:
        return None


def run_query(sql):
    """Execute a read-only query. Returns (columns, rows, error)."""
    try:
        conn = get_connection()
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        conn.close()
        return columns, rows, None
    except sqlite3.Error as e:
        return [], [], str(e)
