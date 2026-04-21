"""
ERP database seed script.

Creates all ERP tables and populates them with ~500 employees using
Pakistani names with demographic and organisational diversity.

Usage:
    python scripts/seed_erp.py            # create tables + seed
    python scripts/seed_erp.py --reset    # drop all ERP tables first, then seed
    python scripts/seed_erp.py --tables-only  # DDL only, no data

Reads DATABASE_URL from environment / .env file.
"""

import argparse
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow running from project root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set. Add it to .env or export it.")
if not DATABASE_URL.startswith(("postgresql://", "postgres://")):
    sys.exit("This script requires PostgreSQL. Set DATABASE_URL to a postgres:// or postgresql:// URL.")

connect_args: dict = {"connect_timeout": 10}
if "sslmode" not in DATABASE_URL:
    connect_args["sslmode"] = "prefer"

engine = create_engine(DATABASE_URL, connect_args=connect_args)

random.seed(42)  # reproducible


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DROP_TABLES = [
    "annual_review_response", "person_skill_category", "skill_category",
    "job_requisition", "core_personemploymenttypehistory",
    "core_personemploymenthistory", "core_personstatushistory",
    "users_personresignation", "person_week_project", "person_week_log",
    "holiday_record", "person_leave_limit", "leave_limit", "leave_record",
    "leave_type", "person_competency", "person_team", "team", "person",
    "designation", "competency_level", "competency_role", "employment_type",
    "department",
]

DDL_CREATE = """
CREATE TABLE IF NOT EXISTS department (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(30) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS employment_type (
    id      SERIAL PRIMARY KEY,
    type    SMALLINT NOT NULL UNIQUE,
    name    VARCHAR(30) NOT NULL
);

CREATE TABLE IF NOT EXISTS competency_role (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS competency_level (
    id      SERIAL PRIMARY KEY,
    name    VARCHAR(50) NOT NULL,
    value   SMALLINT NOT NULL
);

CREATE TABLE IF NOT EXISTS designation (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    core_name   VARCHAR(100),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS leave_type (
    id      SERIAL PRIMARY KEY,
    name    VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_category (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(100) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS person (
    id                          SERIAL PRIMARY KEY,
    person_id                   VARCHAR(20) NOT NULL UNIQUE,
    full_name                   VARCHAR(100) NOT NULL,
    joining_date                DATE NOT NULL,
    career_start_date           DATE,
    experience_start_date       DATE,
    confirmation_date           DATE,
    separation_date             DATE,
    separation_type             SMALLINT,
    separation_reason           TEXT,
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    is_loggable                 BOOLEAN NOT NULL DEFAULT TRUE,
    is_remote                   BOOLEAN NOT NULL DEFAULT FALSE,
    status_id                   INTEGER NOT NULL,
    department_id               INTEGER NOT NULL REFERENCES department(id),
    current_designation_id      INTEGER REFERENCES designation(id),
    employment_type_id          INTEGER NOT NULL REFERENCES employment_type(id),
    notice_period_duration      SMALLINT DEFAULT 30,
    entity_id                   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS team (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    complete_name   TEXT,
    description     TEXT,
    billable        BOOLEAN NOT NULL DEFAULT TRUE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    lead_id         INTEGER REFERENCES person(id),
    start_date      DATE,
    end_date        DATE,
    is_division     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS person_team (
    id              SERIAL PRIMARY KEY,
    person_id       INTEGER NOT NULL REFERENCES person(id),
    nsubteam_id     INTEGER NOT NULL REFERENCES team(id),
    start_date      DATE NOT NULL,
    end_date        DATE,
    billable        BOOLEAN NOT NULL DEFAULT TRUE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_overall_lead BOOLEAN NOT NULL DEFAULT FALSE,
    "primary"       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS leave_limit (
    id                  SERIAL PRIMARY KEY,
    employment_type_id  INTEGER NOT NULL REFERENCES employment_type(id),
    experience_min      SMALLINT NOT NULL DEFAULT 0,
    experience_max      SMALLINT,
    days_allowed        SMALLINT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_leave_limit (
    id          SERIAL PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES person(id),
    leave_type_id INTEGER NOT NULL REFERENCES leave_type(id),
    days_allowed SMALLINT NOT NULL
);

CREATE TABLE IF NOT EXISTS leave_record (
    id              SERIAL PRIMARY KEY,
    person_id       INTEGER NOT NULL REFERENCES person(id),
    leave_type_id   INTEGER NOT NULL REFERENCES leave_type(id),
    start           DATE NOT NULL,
    "end"           DATE NOT NULL,
    status          SMALLINT NOT NULL DEFAULT 0,
    half_day        BOOLEAN NOT NULL DEFAULT FALSE,
    taken           BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS holiday_record (
    id          SERIAL PRIMARY KEY,
    reason      VARCHAR(250) NOT NULL,
    start       DATE NOT NULL,
    "end"       DATE NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS person_week_log (
    id              SERIAL PRIMARY KEY,
    person_id       INTEGER NOT NULL REFERENCES person(id),
    week_starting   DATE NOT NULL,
    hours           INTEGER NOT NULL DEFAULT 0,
    minutes         INTEGER NOT NULL DEFAULT 0,
    is_completed    BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (person_id, week_starting)
);

CREATE TABLE IF NOT EXISTS person_week_project (
    id                  SERIAL PRIMARY KEY,
    person_week_log_id  INTEGER NOT NULL REFERENCES person_week_log(id),
    person_team_id      INTEGER NOT NULL REFERENCES person_team(id),
    hours               INTEGER NOT NULL DEFAULT 0,
    minutes             INTEGER NOT NULL DEFAULT 0,
    is_approved         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS person_competency (
    id              SERIAL PRIMARY KEY,
    person_id       INTEGER NOT NULL REFERENCES person(id),
    role_id         INTEGER NOT NULL REFERENCES competency_role(id),
    overall_level_id INTEGER NOT NULL REFERENCES competency_level(id),
    status          SMALLINT NOT NULL DEFAULT 2,
    is_enabled      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS users_personresignation (
    id                      SERIAL PRIMARY KEY,
    person_id               INTEGER NOT NULL REFERENCES person(id),
    separation_type         SMALLINT NOT NULL,
    status                  SMALLINT NOT NULL DEFAULT 1,
    resignation_date        DATE NOT NULL,
    last_working_day        DATE NOT NULL,
    resignation_reason_type SMALLINT DEFAULT 1,
    resignation_reason      VARCHAR(500)
);

CREATE TABLE IF NOT EXISTS core_personstatushistory (
    id          SERIAL PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES person(id),
    status_id   INTEGER NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE
);

CREATE TABLE IF NOT EXISTS core_personemploymenthistory (
    id                  SERIAL PRIMARY KEY,
    person_id           INTEGER NOT NULL REFERENCES person(id),
    employment_type_id  INTEGER NOT NULL REFERENCES employment_type(id),
    entity_id           INTEGER NOT NULL DEFAULT 1,
    status_id           INTEGER NOT NULL,
    start_date          DATE NOT NULL,
    end_date            DATE
);

CREATE TABLE IF NOT EXISTS core_personemploymenttypehistory (
    id                  SERIAL PRIMARY KEY,
    person_id           INTEGER NOT NULL REFERENCES person(id),
    employment_type_id  INTEGER NOT NULL REFERENCES employment_type(id),
    start_date          DATE NOT NULL,
    end_date            DATE
);

CREATE TABLE IF NOT EXISTS person_skill_category (
    id                  SERIAL PRIMARY KEY,
    person_id           INTEGER NOT NULL REFERENCES person(id),
    skill_category_id   INTEGER NOT NULL REFERENCES skill_category(id),
    skill_title         VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS job_requisition (
    id              SERIAL PRIMARY KEY,
    role_id         INTEGER NOT NULL REFERENCES competency_role(id),
    status          SMALLINT NOT NULL DEFAULT 1,
    min_experience  SMALLINT,
    max_experience  SMALLINT,
    tech_stack      TEXT,
    required_at     DATE,
    subteam_id      INTEGER REFERENCES team(id)
);

CREATE TABLE IF NOT EXISTS annual_review_response (
    id              SERIAL PRIMARY KEY,
    person_team_id  INTEGER NOT NULL REFERENCES person_team(id),
    skill_rate      NUMERIC(4,2)
);
"""


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

DEPARTMENTS = [
    "Engineering", "Quality Assurance", "Product", "DevOps",
    "Human Resources", "Finance", "Sales", "Marketing", "Operations", "Design",
]

EMPLOYMENT_TYPES = [
    (1, "Employee"), (4, "Intern"), (5, "EOR"),
    (2, "Contract"), (3, "Sub-contractor"),
]

COMPETENCY_ROLES = [
    "Software Engineer", "QA Engineer", "Product Manager", "DevOps Engineer",
    "UI/UX Designer", "HR Specialist", "Finance Analyst", "Sales Executive",
    "Engineering Manager", "Marketing Specialist",
]

COMPETENCY_LEVELS = [
    ("Junior", 1), ("Mid", 2), ("Senior", 3), ("Lead", 4),
]

LEAVE_TYPES = ["Annual", "Sick", "Casual", "Maternity", "Paternity", "Unpaid"]

SKILLS = [
    "Python", "JavaScript", "TypeScript", "React", "Vue.js", "Django",
    "FastAPI", "Node.js", "PostgreSQL", "MySQL", "MongoDB", "Redis",
    "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform",
    "CI/CD", "Git", "Linux", "REST APIs", "GraphQL",
    "Figma", "Adobe XD", "Data Analysis", "Excel / Sheets",
    "Salesforce", "HubSpot", "Jira", "Confluence",
]

DESIGNATIONS = [
    # Engineering
    ("Junior Software Engineer",    "Software Engineer"),
    ("Software Engineer",           "Software Engineer"),
    ("Senior Software Engineer",    "Software Engineer"),
    ("Lead Software Engineer",      "Software Engineer"),
    ("Engineering Manager",         "Engineering Manager"),
    # QA
    ("Junior QA Engineer",          "QA Engineer"),
    ("QA Engineer",                 "QA Engineer"),
    ("Senior QA Engineer",          "QA Engineer"),
    ("QA Lead",                     "QA Engineer"),
    # Product
    ("Associate Product Manager",   "Product Manager"),
    ("Product Manager",             "Product Manager"),
    ("Senior Product Manager",      "Product Manager"),
    ("Director of Product",         "Product Manager"),
    # DevOps
    ("DevOps Engineer",             "DevOps Engineer"),
    ("Senior DevOps Engineer",      "DevOps Engineer"),
    ("DevOps Lead",                 "DevOps Engineer"),
    # Design
    ("UI/UX Designer",              "UI/UX Designer"),
    ("Senior UI/UX Designer",       "UI/UX Designer"),
    ("Design Lead",                 "UI/UX Designer"),
    # HR
    ("HR Executive",                "HR Specialist"),
    ("HR Manager",                  "HR Specialist"),
    ("Talent Acquisition Specialist", "HR Specialist"),
    # Finance
    ("Finance Executive",           "Finance Analyst"),
    ("Finance Manager",             "Finance Analyst"),
    ("Senior Finance Analyst",      "Finance Analyst"),
    # Sales
    ("Sales Executive",             "Sales Executive"),
    ("Senior Sales Executive",      "Sales Executive"),
    ("Sales Manager",               "Sales Executive"),
    # Marketing
    ("Marketing Executive",         "Marketing Specialist"),
    ("Content Writer",              "Marketing Specialist"),
    ("Digital Marketing Specialist","Marketing Specialist"),
    # Operations
    ("Operations Executive",        "Operations Specialist"),
    ("Operations Manager",          "Operations Specialist"),
    ("Logistics Coordinator",       "Operations Specialist"),
    # Leadership
    ("CTO",                         "Engineering Manager"),
    ("CEO",                         "Engineering Manager"),
    ("CHRO",                        "HR Specialist"),
    ("CFO",                         "Finance Analyst"),
    ("Head of Sales",               "Sales Executive"),
]

TEAMS = [
    # Active billable client projects
    ("Nexus ERP Integration",   True,  True),
    ("FinTrack Mobile",         True,  True),
    ("HealthBridge Platform",   True,  True),
    ("RetailMax Analytics",     True,  True),
    ("LogiCore Supply Chain",   True,  True),
    ("EduSpark LMS",            True,  True),
    ("PayWave Gateway",         True,  True),
    ("SmartHR Suite",           True,  True),
    ("CloudVault Storage",      True,  True),
    ("AgroSense IoT",           True,  True),
    ("CivicConnect Portal",     True,  True),
    ("TelcoBI Dashboard",       True,  True),
    ("InsureFlow Platform",     True,  True),
    # Completed projects (inactive)
    ("LegacyMigrate 2022",      True,  False),
    ("DataPipeline Rebuild",    True,  False),
    ("Mobile Revamp Q1-2023",   True,  False),
    # Internal / non-billable
    ("Internal Tools",          False, True),
    ("R&D / Innovation",        False, True),
    ("Company Bench",           False, True),
    ("HR & Admin Ops",          False, True),
    ("Sales Enablement",        False, True),
    ("Marketing Campaigns",     False, True),
]

# Separate first/last name pools for uniqueness
MALE_FIRST_NAMES = [
    "Muhammad", "Ahmed", "Usman", "Bilal", "Faisal", "Hamza", "Kamran",
    "Naveed", "Shahid", "Waqas", "Junaid", "Saad", "Rizwan", "Adeel",
    "Arslan", "Talha", "Sohail", "Imran", "Tariq", "Noman", "Asif",
    "Zulfiqar", "Rafique", "Javed", "Saleem", "Arif", "Riaz", "Khalid",
    "Omar", "Samiullah", "Zahir", "Hidayat", "Rashid", "Wali", "Farooq",
    "Zubair", "Arshad", "Mansoor", "Dawood", "Habib", "Sardar", "Yousuf",
    "Ghulam", "Naseer", "Zain", "Hassan", "Mujtaba", "Asad", "Raza",
    "Kazim", "Omer", "Farrukh", "Sajid", "Amir", "Saqib", "Taha",
    "Owais", "Waseem", "Fawad", "Nasir", "Shoaib", "Irfan", "Abubakar",
    "Daniyal", "Haider", "Jawad", "Mubashir", "Rehan", "Zeeshan", "Ahsan",
    "Babar", "Farhan", "Kaleem", "Luqman", "Maaz", "Nabeel", "Parvez",
    "Qasim", "Raees", "Shafiq", "Tanveer", "Umer", "Vikram", "Yaar",
]

FEMALE_FIRST_NAMES = [
    "Ayesha", "Zara", "Sara", "Hina", "Maryam", "Nadia", "Sana",
    "Amna", "Rabia", "Kiran", "Mahnoor", "Nimra", "Alina", "Shazia",
    "Rimsha", "Sobia", "Urooj", "Mehwish", "Noor", "Sidra", "Mariam",
    "Sadia", "Naila", "Fouzia", "Lubna", "Shaista", "Nazish", "Razia",
    "Palwasha", "Gul", "Rukhsana", "Humaira", "Samina", "Shahnaz",
    "Zarghona", "Naseem", "Zeenat", "Bibi", "Shaheena", "Fatima",
    "Sundus", "Saba", "Layla", "Nawal", "Aisha", "Zainab", "Anum",
    "Hira", "Dua", "Tuba", "Iram", "Bushra", "Gulnaz", "Ifra",
    "Javeria", "Komal", "Musarrat", "Nargis", "Oreeba", "Qurat",
    "Riffat", "Saima", "Tahira", "Uzma", "Veena", "Warda", "Yasmeen",
]

LAST_NAMES_PUNJABI   = ["Khan", "Malik", "Ahmed", "Chaudhry", "Gondal", "Mirza", "Niazi",
                        "Qureshi", "Rafique", "Warraich", "Butt", "Gill", "Rana", "Jatt",
                        "Cheema", "Awan", "Gujjar", "Baig", "Syed", "Hashmi"]
LAST_NAMES_SINDHI    = ["Soomro", "Memon", "Chandio", "Panhwar", "Laghari", "Shaikh",
                        "Brohi", "Talpur", "Khuhro", "Bhutto", "Siyal", "Abro", "Kalhoro"]
LAST_NAMES_PASHTUN   = ["Yousafzai", "Afridi", "Shinwari", "Mohmand", "Khattak", "Durrani",
                        "Wazir", "Barakzai", "Swati", "Marwat", "Orakzai", "Bangash"]
LAST_NAMES_BALOCHI   = ["Mengal", "Bugti", "Marri", "Rind", "Zehri", "Lehri",
                        "Bizenjo", "Raisani", "Baloch", "Mirwani", "Dashti"]
LAST_NAMES_URDU      = ["Siddiqui", "Farooqi", "Zaidi", "Naqvi", "Hamdani", "Rizvi",
                        "Abbasi", "Ansari", "Kazmi", "Hussain", "Ali", "Rehman", "Hassan",
                        "Badar", "Akhtar", "Jafri", "Tirmizi", "Qadri", "Gillani"]

ALL_LAST_NAMES = (
    LAST_NAMES_PUNJABI * 4
    + LAST_NAMES_SINDHI * 2
    + LAST_NAMES_PASHTUN * 2
    + LAST_NAMES_BALOCHI
    + LAST_NAMES_URDU * 2
)

PAKISTANI_HOLIDAYS = [
    # 2022
    ("Kashmir Solidarity Day",  date(2022, 2, 5),  date(2022, 2, 5)),
    ("Pakistan Day",            date(2022, 3, 23), date(2022, 3, 23)),
    ("Labour Day",              date(2022, 5, 1),  date(2022, 5, 1)),
    ("Eid ul-Fitr 2022",        date(2022, 5, 2),  date(2022, 5, 4)),
    ("Eid ul-Adha 2022",        date(2022, 7, 9),  date(2022, 7, 11)),
    ("Independence Day 2022",   date(2022, 8, 14), date(2022, 8, 14)),
    ("Eid Milad-un-Nabi 2022",  date(2022, 10, 9), date(2022, 10, 9)),
    ("Iqbal Day",               date(2022, 11, 9), date(2022, 11, 9)),
    ("Christmas / Quaid Day",   date(2022, 12, 25),date(2022, 12, 25)),
    # 2023
    ("Kashmir Solidarity Day",  date(2023, 2, 5),  date(2023, 2, 5)),
    ("Pakistan Day",            date(2023, 3, 23), date(2023, 3, 23)),
    ("Eid ul-Fitr 2023",        date(2023, 4, 21), date(2023, 4, 23)),
    ("Labour Day",              date(2023, 5, 1),  date(2023, 5, 1)),
    ("Eid ul-Adha 2023",        date(2023, 6, 28), date(2023, 6, 30)),
    ("Independence Day 2023",   date(2023, 8, 14), date(2023, 8, 14)),
    ("Eid Milad-un-Nabi 2023",  date(2023, 9, 28), date(2023, 9, 28)),
    ("Iqbal Day",               date(2023, 11, 9), date(2023, 11, 9)),
    ("Christmas / Quaid Day",   date(2023, 12, 25),date(2023, 12, 25)),
    # 2024
    ("Kashmir Solidarity Day",  date(2024, 2, 5),  date(2024, 2, 5)),
    ("Pakistan Day",            date(2024, 3, 23), date(2024, 3, 23)),
    ("Eid ul-Fitr 2024",        date(2024, 4, 10), date(2024, 4, 12)),
    ("Labour Day",              date(2024, 5, 1),  date(2024, 5, 1)),
    ("Eid ul-Adha 2024",        date(2024, 6, 17), date(2024, 6, 19)),
    ("Independence Day 2024",   date(2024, 8, 14), date(2024, 8, 14)),
    ("Eid Milad-un-Nabi 2024",  date(2024, 9, 16), date(2024, 9, 16)),
    ("Iqbal Day",               date(2024, 11, 9), date(2024, 11, 9)),
    ("Christmas / Quaid Day",   date(2024, 12, 25),date(2024, 12, 25)),
    # 2025
    ("Kashmir Solidarity Day",  date(2025, 2, 5),  date(2025, 2, 5)),
    ("Pakistan Day",            date(2025, 3, 23), date(2025, 3, 23)),
    ("Eid ul-Fitr 2025",        date(2025, 3, 30), date(2025, 4, 1)),
    ("Labour Day",              date(2025, 5, 1),  date(2025, 5, 1)),
    ("Eid ul-Adha 2025",        date(2025, 6, 6),  date(2025, 6, 8)),
    ("Independence Day 2025",   date(2025, 8, 14), date(2025, 8, 14)),
]

RESIGNATION_REASONS = [
    "Better opportunity abroad", "Higher compensation elsewhere",
    "Pursuing higher education", "Relocation to another city",
    "Personal reasons", "Career change", "Family commitments",
    "Work-life balance concerns", "Company culture mismatch",
    "Limited growth opportunities",
]

TERMINATION_REASONS = [
    "Performance issues", "Policy violation", "Redundancy",
    "Contract non-renewal", "Restructuring",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rand_date(start: date, end: date) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def next_monday(d: date) -> date:
    """Return the Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def weighted_choice(choices, weights):
    total = sum(weights)
    r = random.uniform(0, total)
    cumulative = 0
    for choice, weight in zip(choices, weights):
        cumulative += weight
        if r <= cumulative:
            return choice
    return choices[-1]


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def seed_lookups(conn):
    print("  Seeding lookup tables...")

    conn.execute(text("""
        INSERT INTO department (name) VALUES
        ('Engineering'), ('Quality Assurance'), ('Product'), ('DevOps'),
        ('Human Resources'), ('Finance'), ('Sales'), ('Marketing'),
        ('Operations'), ('Design')
        ON CONFLICT DO NOTHING
    """))

    for type_val, type_name in EMPLOYMENT_TYPES:
        conn.execute(text(
            "INSERT INTO employment_type (type, name) VALUES (:t, :n) ON CONFLICT (type) DO NOTHING"
        ), {"t": type_val, "n": type_name})

    for role in COMPETENCY_ROLES:
        conn.execute(text(
            "INSERT INTO competency_role (name) VALUES (:n)"
        ), {"n": role})

    for level_name, level_val in COMPETENCY_LEVELS:
        conn.execute(text(
            "INSERT INTO competency_level (name, value) VALUES (:n, :v)"
        ), {"n": level_name, "v": level_val})

    for lt in LEAVE_TYPES:
        conn.execute(text("INSERT INTO leave_type (name) VALUES (:n)"), {"n": lt})

    for skill in SKILLS:
        conn.execute(text("INSERT INTO skill_category (title) VALUES (:t)"), {"t": skill})

    for title, core in DESIGNATIONS:
        conn.execute(text(
            "INSERT INTO designation (name, core_name) VALUES (:n, :c)"
        ), {"n": title, "c": core})

    print("    Lookups done.")


def seed_holidays(conn):
    print("  Seeding holidays...")
    for reason, start, end in PAKISTANI_HOLIDAYS:
        conn.execute(text(
            "INSERT INTO holiday_record (reason, start, \"end\") VALUES (:r, :s, :e)"
        ), {"r": reason, "s": start, "e": end})


def load_ids(conn):
    """Load all lookup IDs into dicts for fast access."""
    def fetch(q):
        return {row[0]: row[1] for row in conn.execute(text(q)).fetchall()}

    dept_ids     = fetch("SELECT name, id FROM department")
    et_ids       = fetch("SELECT type, id FROM employment_type")
    role_ids     = fetch("SELECT name, id FROM competency_role")
    level_ids    = fetch("SELECT name, id FROM competency_level")
    lt_ids       = fetch("SELECT name, id FROM leave_type")
    skill_ids    = fetch("SELECT title, id FROM skill_category")
    desig_ids    = fetch("SELECT name, id FROM designation")

    return dept_ids, et_ids, role_ids, level_ids, lt_ids, skill_ids, desig_ids


def make_person_pool():
    """Generate 560 people using first × last name combinations for uniqueness."""
    last_pool = ALL_LAST_NAMES[:]
    random.shuffle(last_pool)

    used: set[str] = set()
    pool = []

    male_firsts = MALE_FIRST_NAMES[:]
    female_firsts = FEMALE_FIRST_NAMES[:]
    random.shuffle(male_firsts)
    random.shuffle(female_firsts)

    def gen(gender: str, first_list: list, target: int):
        idx_f = 0
        idx_l = 0
        while len([p for p in pool if p[0] == gender]) < target:
            first = first_list[idx_f % len(first_list)]
            last = last_pool[idx_l % len(last_pool)]
            full = f"{first} {last}"
            if full not in used:
                used.add(full)
                pool.append((gender, full, "mixed"))
            idx_f += 1
            idx_l += 1
            if idx_f > len(first_list) * len(last_pool):
                break  # safety exit

    gen("M", male_firsts, 305)
    gen("F", female_firsts, 255)
    random.shuffle(pool)
    return pool


# Dept → competency role mapping (most likely role for each dept)
DEPT_ROLE_MAP = {
    "Engineering":      ["Software Engineer", "Engineering Manager"],
    "Quality Assurance":["QA Engineer"],
    "Product":          ["Product Manager"],
    "DevOps":           ["DevOps Engineer"],
    "Human Resources":  ["HR Specialist"],
    "Finance":          ["Finance Analyst"],
    "Sales":            ["Sales Executive"],
    "Marketing":        ["Marketing Specialist"],
    "Operations":       ["Marketing Specialist", "HR Specialist"],
    "Design":           ["UI/UX Designer"],
}

# Dept → designation name patterns
DEPT_DESIG_MAP = {
    "Engineering":       ["Junior Software Engineer", "Software Engineer", "Senior Software Engineer", "Lead Software Engineer", "Engineering Manager"],
    "Quality Assurance": ["Junior QA Engineer", "QA Engineer", "Senior QA Engineer", "QA Lead"],
    "Product":           ["Associate Product Manager", "Product Manager", "Senior Product Manager", "Director of Product"],
    "DevOps":            ["DevOps Engineer", "Senior DevOps Engineer", "DevOps Lead"],
    "Human Resources":   ["HR Executive", "Talent Acquisition Specialist", "HR Manager", "CHRO"],
    "Finance":           ["Finance Executive", "Senior Finance Analyst", "Finance Manager", "CFO"],
    "Sales":             ["Sales Executive", "Senior Sales Executive", "Sales Manager", "Head of Sales"],
    "Marketing":         ["Marketing Executive", "Content Writer", "Digital Marketing Specialist"],
    "Operations":        ["Operations Executive", "Operations Manager", "Logistics Coordinator"],
    "Design":            ["UI/UX Designer", "Senior UI/UX Designer", "Design Lead"],
}

# Department size weights (out of 500)
DEPT_WEIGHTS = {
    "Engineering": 190, "Quality Assurance": 50, "Product": 40, "DevOps": 35,
    "Human Resources": 25, "Finance": 25, "Sales": 40, "Marketing": 30,
    "Operations": 35, "Design": 30,
}

# Employment type weights
ET_WEIGHTS = [(1, 72), (4, 8), (5, 5), (2, 10), (3, 5)]  # (type, weight)

TODAY = date(2025, 4, 17)


def seed_people(conn, dept_ids, et_ids, desig_ids):
    print("  Seeding 500 people...")
    pool = make_person_pool()

    # Pre-build dept assignment list respecting weights
    dept_list = []
    for dept, weight in DEPT_WEIGHTS.items():
        dept_list.extend([dept] * weight)
    random.shuffle(dept_list)

    persons = []  # list of dicts for later reference

    for i, (gender, full_name, _origin) in enumerate(pool[:500]):
        emp_num = i + 1
        person_id_str = f"EMP-{emp_num:04d}"
        dept_name = dept_list[i]

        # Employment type
        et_type = weighted_choice(
            [t for t, _ in ET_WEIGHTS],
            [w for _, w in ET_WEIGHTS]
        )
        et_id = et_ids[et_type]

        # Joining date — weighted toward 2021-2023
        year_weights = [
            (2019, 5), (2020, 8), (2021, 20), (2022, 25),
            (2023, 22), (2024, 15), (2025, 5),
        ]
        join_year = weighted_choice([y for y, _ in year_weights], [w for _, w in year_weights])
        join_date = rand_date(date(join_year, 1, 1), date(join_year, 12, 31))
        if join_date > TODAY:
            join_date = TODAY - timedelta(days=random.randint(1, 30))

        # Career start (1-8 years before joining)
        career_years_before = random.randint(0, 8)
        career_start = join_date - timedelta(days=career_years_before * 365 + random.randint(0, 90))

        # Confirmation date: 3-6 months after joining (only for full employees)
        confirmation = None
        if et_type == 1:
            confirmation = join_date + timedelta(days=random.randint(90, 180))
            if confirmation > TODAY:
                confirmation = None

        # Designation
        desig_options = DEPT_DESIG_MAP[dept_name]
        desig_name = random.choice(desig_options)
        desig_id = desig_ids.get(desig_name)

        # Status: 80 people exit (last 80 in shuffled order = exit cohort)
        is_exited = emp_num > 420
        if is_exited:
            max_exit = TODAY - timedelta(days=30)
            min_exit = join_date + timedelta(days=180)
            if min_exit >= max_exit:
                # Joined too recently to have exited — force join date back
                join_date = max_exit - timedelta(days=random.randint(270, 730))
                min_exit = join_date + timedelta(days=180)
            sep_date = rand_date(min_exit, max_exit)
            sep_type = random.choices([2, 3, 4], weights=[65, 20, 15])[0]
            status_id = {2: 11, 3: 12, 4: 20}[sep_type]
            is_active = False
        else:
            sep_date = None
            sep_type = None
            status_id = random.choices([10, 22, 17], weights=[75, 15, 10])[0]
            is_active = True

        is_remote = random.random() < 0.18
        is_loggable = et_type not in (3,)  # sub-contractors not loggable

        p = {
            "person_id": person_id_str,
            "full_name": full_name,
            "joining_date": join_date,
            "career_start_date": career_start,
            "experience_start_date": career_start,
            "confirmation_date": confirmation,
            "separation_date": sep_date,
            "separation_type": sep_type,
            "is_active": is_active,
            "is_loggable": is_loggable,
            "is_remote": is_remote,
            "status_id": status_id,
            "department_id": dept_ids[dept_name],
            "current_designation_id": desig_id,
            "employment_type_id": et_id,
            "notice_period_duration": 30 if et_type in (1, 5) else 14,
            "entity_id": 1,
            "dept_name": dept_name,
            "et_type": et_type,
            "is_exited": is_exited,
            "gender": gender,
        }
        persons.append(p)

    # Batch insert
    for p in persons:
        conn.execute(text("""
            INSERT INTO person (
                person_id, full_name, joining_date, career_start_date,
                experience_start_date, confirmation_date, separation_date,
                separation_type, is_active, is_loggable, is_remote,
                status_id, department_id, current_designation_id,
                employment_type_id, notice_period_duration, entity_id
            ) VALUES (
                :person_id, :full_name, :joining_date, :career_start_date,
                :experience_start_date, :confirmation_date, :separation_date,
                :separation_type, :is_active, :is_loggable, :is_remote,
                :status_id, :department_id, :current_designation_id,
                :employment_type_id, :notice_period_duration, :entity_id
            )
        """), p)

    # Fetch back DB IDs
    rows = conn.execute(text("SELECT id, person_id FROM person ORDER BY id")).fetchall()
    id_map = {row[1]: row[0] for row in rows}
    for p in persons:
        p["db_id"] = id_map[p["person_id"]]

    print(f"    Inserted {len(persons)} people.")
    return persons


def seed_teams(conn, persons, dept_ids):
    print("  Seeding teams...")

    # Pick leads from senior active engineers / dept heads
    active = [p for p in persons if not p["is_exited"]]
    senior = [p for p in active if p["dept_name"] == "Engineering"]
    if len(senior) < len(TEAMS):
        senior = active

    team_ids = []
    for i, (name, billable, is_active) in enumerate(TEAMS):
        lead = senior[i % len(senior)]
        start = rand_date(date(2020, 1, 1), date(2023, 6, 1))
        end = None
        if not is_active:
            end = rand_date(date(2023, 1, 1), date(2024, 6, 1))
        row = conn.execute(text("""
            INSERT INTO team (name, complete_name, billable, is_active, lead_id, start_date, end_date, is_division)
            VALUES (:name, :name, :billable, :is_active, :lead_id, :start_date, :end_date, false)
            RETURNING id
        """), {
            "name": name, "billable": billable, "is_active": is_active,
            "lead_id": lead["db_id"], "start_date": start, "end_date": end,
        })
        team_ids.append(row.fetchone()[0])

    print(f"    Inserted {len(team_ids)} teams.")
    return team_ids


def seed_person_teams(conn, persons, team_ids):
    print("  Seeding person_team assignments...")

    active_teams = team_ids[:13]     # first 13 are active billable
    bench_team = team_ids[18]        # "Company Bench"
    internal_team = team_ids[16]     # "Internal Tools"

    # Non-billable teams by dept
    dept_nonbillable = {
        "Human Resources": team_ids[19],
        "Sales": team_ids[20],
        "Marketing": team_ids[21],
    }

    pt_rows = []
    for p in persons:
        if p["is_exited"]:
            # Assign to a random active team during tenure
            team_id = random.choice(active_teams)
            start = p["joining_date"] + timedelta(days=random.randint(7, 30))
            end = p["separation_date"]
            pt_rows.append({
                "person_id": p["db_id"], "nsubteam_id": team_id,
                "start_date": start, "end_date": end,
                "billable": True, "is_active": False,
                "is_overall_lead": False, "primary": True,
            })
            continue

        dept = p["dept_name"]
        start = p["joining_date"] + timedelta(days=random.randint(7, 30))

        if dept in dept_nonbillable:
            # HR / Sales / Marketing go to their own non-billable team
            team_id = dept_nonbillable[dept]
            pt_rows.append({
                "person_id": p["db_id"], "nsubteam_id": team_id,
                "start_date": start, "end_date": None,
                "billable": False, "is_active": True,
                "is_overall_lead": False, "primary": True,
            })
        elif random.random() < 0.12:
            # ~12% of active engineering/product/qa are on bench
            pt_rows.append({
                "person_id": p["db_id"], "nsubteam_id": bench_team,
                "start_date": start, "end_date": None,
                "billable": False, "is_active": True,
                "is_overall_lead": False, "primary": True,
            })
        else:
            team_id = random.choice(active_teams)
            is_lead = random.random() < 0.05
            pt_rows.append({
                "person_id": p["db_id"], "nsubteam_id": team_id,
                "start_date": start, "end_date": None,
                "billable": True, "is_active": True,
                "is_overall_lead": is_lead, "primary": True,
            })

            # ~20% also have a secondary assignment
            if random.random() < 0.20 and dept in ("Engineering", "Quality Assurance", "DevOps"):
                secondary = random.choice([t for t in active_teams if t != team_id])
                pt_rows.append({
                    "person_id": p["db_id"], "nsubteam_id": secondary,
                    "start_date": start + timedelta(days=random.randint(30, 90)),
                    "end_date": None,
                    "billable": True, "is_active": True,
                    "is_overall_lead": False, "primary": False,
                })

    pt_ids = []
    for row in pt_rows:
        r = conn.execute(text("""
            INSERT INTO person_team (person_id, nsubteam_id, start_date, end_date,
                billable, is_active, is_overall_lead, "primary")
            VALUES (:person_id, :nsubteam_id, :start_date, :end_date,
                :billable, :is_active, :is_overall_lead, :primary)
            RETURNING id
        """), row)
        pt_ids.append((row["person_id"], r.fetchone()[0]))

    print(f"    Inserted {len(pt_rows)} person_team rows.")
    return pt_ids  # list of (person_db_id, person_team_id)


def seed_competencies(conn, persons, role_ids, level_ids):
    print("  Seeding competencies...")
    for p in persons:
        if p["is_exited"] and random.random() < 0.3:
            continue  # ~30% of exited have no competency record
        role_options = DEPT_ROLE_MAP.get(p["dept_name"], ["Software Engineer"])
        role_name = random.choice(role_options)
        role_id = role_ids[role_name]

        # Seniority from tenure
        tenure_days = ((p["separation_date"] or TODAY) - p["joining_date"]).days
        tenure_years = tenure_days / 365
        if tenure_years < 1.5:
            level_name = "Junior"
        elif tenure_years < 3.5:
            level_name = "Mid"
        elif tenure_years < 6:
            level_name = "Senior"
        else:
            level_name = "Lead"

        level_id = level_ids[level_name]
        conn.execute(text("""
            INSERT INTO person_competency (person_id, role_id, overall_level_id, status, is_enabled)
            VALUES (:pid, :rid, :lid, 2, true)
        """), {"pid": p["db_id"], "rid": role_id, "lid": level_id})


def seed_leave_records(conn, persons, lt_ids):
    print("  Seeding leave records...")
    leave_type_list = list(lt_ids.items())

    count = 0
    for p in persons:
        end_bound = p["separation_date"] or TODAY
        if (end_bound - p["joining_date"]).days < 30:
            continue

        leave_start_min = max(p["joining_date"] + timedelta(days=30), date(2022, 1, 1))
        leave_start_max = end_bound - timedelta(days=1)
        if leave_start_min >= leave_start_max:
            continue

        n_leaves = random.randint(1, 6)
        for _ in range(n_leaves):
            lt_name, lt_id = random.choice(leave_type_list)
            if lt_name == "Maternity" and p["gender"] != "F":
                lt_name, lt_id = "Annual", lt_ids["Annual"]
            if lt_name == "Paternity" and p["gender"] != "M":
                lt_name, lt_id = "Sick", lt_ids["Sick"]

            start = rand_date(leave_start_min, leave_start_max)
            duration = random.choices([1, 2, 3, 5, 14], weights=[30, 25, 20, 15, 10])[0]
            if lt_name == "Maternity":
                duration = 90
            end = min(start + timedelta(days=duration - 1), end_bound)
            status = random.choices([0, 1, 2], weights=[10, 80, 10])[0]
            taken = status == 1 and end < TODAY

            conn.execute(text("""
                INSERT INTO leave_record (person_id, leave_type_id, start, "end", status, half_day, taken)
                VALUES (:pid, :ltid, :start, :end, :status, :half_day, :taken)
            """), {
                "pid": p["db_id"], "ltid": lt_id,
                "start": start, "end": end,
                "status": status, "half_day": duration == 1 and random.random() < 0.3,
                "taken": taken,
            })
            count += 1

    print(f"    Inserted {count} leave records.")


def seed_week_logs(conn, persons, pt_ids_map):
    """
    Seed 16 weeks of time logs. ~85% compliance for active loggable employees.
    pt_ids_map: person_db_id → list of person_team_ids
    """
    print("  Seeding weekly logs (16 weeks)...")

    # Build list of Mondays
    mondays = []
    d = TODAY - timedelta(weeks=16)
    d = d - timedelta(days=d.weekday())
    while d < TODAY:
        mondays.append(d)
        d += timedelta(weeks=1)

    log_count = 0
    proj_count = 0

    loggable = [p for p in persons if not p["is_exited"] and p["is_loggable"]]

    for p in loggable:
        pt_list = pt_ids_map.get(p["db_id"], [])
        if not pt_list:
            continue

        for monday in mondays:
            if monday < p["joining_date"]:
                continue
            # 15% miss logs
            is_completed = random.random() > 0.15
            total_hours = random.randint(35, 45) if is_completed else random.randint(0, 10)
            total_minutes = random.randint(0, 59)

            row = conn.execute(text("""
                INSERT INTO person_week_log (person_id, week_starting, hours, minutes, is_completed)
                VALUES (:pid, :ws, :h, :m, :ic)
                ON CONFLICT (person_id, week_starting) DO NOTHING
                RETURNING id
            """), {"pid": p["db_id"], "ws": monday, "h": total_hours, "m": total_minutes, "ic": is_completed})
            r = row.fetchone()
            if not r:
                continue
            log_id = r[0]
            log_count += 1

            # Distribute hours across assigned teams
            n_teams = min(len(pt_list), random.randint(1, 2))
            assigned = random.sample(pt_list, n_teams)
            remaining = total_hours
            for j, pt_id in enumerate(assigned):
                h = remaining if j == len(assigned) - 1 else random.randint(5, max(5, remaining - 5))
                h = max(0, min(h, remaining))
                remaining -= h
                conn.execute(text("""
                    INSERT INTO person_week_project (person_week_log_id, person_team_id, hours, minutes, is_approved)
                    VALUES (:lid, :ptid, :h, :m, :ia)
                """), {"lid": log_id, "ptid": pt_id, "h": h, "m": random.randint(0, 59), "ia": is_completed})
                proj_count += 1

    print(f"    Inserted {log_count} log headers, {proj_count} project entries.")


def seed_resignations(conn, persons):
    print("  Seeding resignation records...")
    exited = [p for p in persons if p["is_exited"]]
    count = 0
    for p in exited:
        sep_type = p["separation_type"]
        sep_date = p["separation_date"]
        resignation_date = sep_date - timedelta(days=random.randint(14, 60))
        reason = (
            random.choice(RESIGNATION_REASONS) if sep_type == 2
            else random.choice(TERMINATION_REASONS)
        )
        conn.execute(text("""
            INSERT INTO users_personresignation
                (person_id, separation_type, status, resignation_date, last_working_day,
                 resignation_reason_type, resignation_reason)
            VALUES (:pid, :st, 1, :rd, :lwd, :rrt, :rr)
        """), {
            "pid": p["db_id"], "st": sep_type,
            "rd": resignation_date, "lwd": sep_date,
            "rrt": random.randint(1, 5), "rr": reason,
        })
        count += 1
    print(f"    Inserted {count} resignation records.")


def seed_status_history(conn, persons):
    print("  Seeding status history...")
    count = 0
    for p in persons:
        # Initial status: Probation for 3 months, then Active
        prob_end = p["joining_date"] + timedelta(days=90)
        conn.execute(text("""
            INSERT INTO core_personstatushistory (person_id, status_id, start_date, end_date)
            VALUES (:pid, 17, :s, :e)
        """), {"pid": p["db_id"], "s": p["joining_date"], "e": prob_end})

        if p["is_exited"]:
            conn.execute(text("""
                INSERT INTO core_personstatushistory (person_id, status_id, start_date, end_date)
                VALUES (:pid, 10, :s, :e)
            """), {"pid": p["db_id"], "s": prob_end, "e": p["separation_date"]})
            exit_status = {2: 11, 3: 12, 4: 20}.get(p["separation_type"], 11)
            conn.execute(text("""
                INSERT INTO core_personstatushistory (person_id, status_id, start_date, end_date)
                VALUES (:pid, :sid, :s, NULL)
            """), {"pid": p["db_id"], "sid": exit_status, "s": p["separation_date"]})
        else:
            conn.execute(text("""
                INSERT INTO core_personstatushistory (person_id, status_id, start_date, end_date)
                VALUES (:pid, :sid, :s, NULL)
            """), {"pid": p["db_id"], "sid": p["status_id"], "s": prob_end})
        count += 1
    print(f"    Inserted status history for {count} people.")


def seed_employment_history(conn, persons):
    print("  Seeding employment history...")
    for p in persons:
        conn.execute(text("""
            INSERT INTO core_personemploymenthistory
                (person_id, employment_type_id, entity_id, status_id, start_date, end_date)
            VALUES (:pid, :etid, 1, :sid, :s, :e)
        """), {
            "pid": p["db_id"], "etid": p["employment_type_id"],
            "sid": p["status_id"], "s": p["joining_date"],
            "e": p["separation_date"],
        })
        conn.execute(text("""
            INSERT INTO core_personemploymenttypehistory
                (person_id, employment_type_id, start_date, end_date)
            VALUES (:pid, :etid, :s, :e)
        """), {
            "pid": p["db_id"], "etid": p["employment_type_id"],
            "s": p["joining_date"], "e": p["separation_date"],
        })


def seed_skills(conn, persons, skill_ids):
    print("  Seeding person skills...")

    dept_skill_map = {
        "Engineering":       ["Python", "JavaScript", "Django", "FastAPI", "React", "PostgreSQL", "Git", "Docker", "REST APIs"],
        "Quality Assurance": ["Python", "Jira", "REST APIs", "Git", "PostgreSQL"],
        "Product":           ["Jira", "Confluence", "Data Analysis", "REST APIs"],
        "DevOps":            ["Docker", "Kubernetes", "Terraform", "AWS", "Linux", "CI/CD", "GCP", "Azure"],
        "Human Resources":   ["Excel / Sheets", "Confluence", "Data Analysis"],
        "Finance":           ["Excel / Sheets", "Data Analysis", "PostgreSQL"],
        "Sales":             ["Salesforce", "HubSpot", "Excel / Sheets"],
        "Marketing":         ["HubSpot", "Data Analysis", "Excel / Sheets", "Figma"],
        "Operations":        ["Excel / Sheets", "Jira", "Confluence"],
        "Design":            ["Figma", "Adobe XD", "JavaScript", "React", "Vue.js"],
    }

    count = 0
    for p in persons:
        dept_skills = dept_skill_map.get(p["dept_name"], list(skill_ids.keys()))
        n = random.randint(2, min(5, len(dept_skills)))
        chosen = random.sample(dept_skills, n)
        for skill_title in chosen:
            sid = skill_ids.get(skill_title)
            if sid:
                conn.execute(text("""
                    INSERT INTO person_skill_category (person_id, skill_category_id, skill_title)
                    VALUES (:pid, :scid, :st)
                """), {"pid": p["db_id"], "scid": sid, "st": skill_title})
                count += 1
    print(f"    Inserted {count} person-skill rows.")


def seed_job_requisitions(conn, role_ids, team_ids):
    print("  Seeding job requisitions...")
    roles = list(role_ids.items())
    for i in range(20):
        role_name, role_id = random.choice(roles)
        status = 1 if i < 12 else 2  # 12 open, 8 closed
        required_at = TODAY + timedelta(days=random.randint(14, 90)) if status == 1 else None
        conn.execute(text("""
            INSERT INTO job_requisition (role_id, status, min_experience, max_experience, tech_stack, required_at, subteam_id)
            VALUES (:rid, :st, :mn, :mx, :ts, :ra, :stid)
        """), {
            "rid": role_id, "st": status,
            "mn": random.randint(1, 3), "mx": random.randint(4, 8),
            "ts": "Python, Django, PostgreSQL" if "Engineer" in role_name else role_name,
            "ra": required_at,
            "stid": random.choice(team_ids[:13]),
        })
    print("    Inserted 20 job requisitions.")


def seed_annual_reviews(conn, pt_ids_flat):
    """
    Seed annual review responses for 2023 and 2024 review cycles.
    pt_ids_flat: flat list of person_team_ids
    """
    print("  Seeding annual reviews...")
    count = 0
    sample_size = min(len(pt_ids_flat), 600)
    sampled = random.sample(pt_ids_flat, sample_size)
    for pt_id in sampled:
        conn.execute(text("""
            INSERT INTO annual_review_response (person_team_id, skill_rate)
            VALUES (:ptid, :sr)
        """), {"ptid": pt_id, "sr": round(random.uniform(2.0, 5.0), 2)})
        count += 1
    print(f"    Inserted {count} annual review responses.")


def seed_leave_limits(conn, et_ids, lt_ids):
    print("  Seeding leave limits...")
    annual_id = lt_ids["Annual"]
    for et_type, et_id in et_ids.items():
        # 0-2 years: 15 days, 2-5: 18 days, 5+: 21 days
        for exp_min, exp_max, days in [(0, 2, 15), (2, 5, 18), (5, None, 21)]:
            conn.execute(text("""
                INSERT INTO leave_limit (employment_type_id, experience_min, experience_max, days_allowed)
                VALUES (:etid, :mn, :mx, :d)
            """), {"etid": et_id, "mn": exp_min, "mx": exp_max, "d": days})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(reset: bool, tables_only: bool):
    with engine.begin() as conn:
        if reset:
            print("Dropping existing ERP tables...")
            for tbl in _DROP_TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
            print("  Done.")

        print("Creating ERP tables...")
        for stmt in [s.strip() for s in DDL_CREATE.split(";") if s.strip()]:
            conn.execute(text(stmt))
        print("  Done.")

        if tables_only:
            print("--tables-only: skipping seed data.")
            return

        print("Seeding data...")
        seed_lookups(conn)
        seed_holidays(conn)

        dept_ids, et_ids, role_ids, level_ids, lt_ids, skill_ids, desig_ids = load_ids(conn)

        seed_leave_limits(conn, et_ids, lt_ids)

        persons = seed_people(conn, dept_ids, et_ids, desig_ids)
        team_ids = seed_teams(conn, persons, dept_ids)
        pt_pairs = seed_person_teams(conn, persons, team_ids)

        # Build person_db_id → [person_team_ids] map
        pt_ids_map = {}
        pt_ids_flat = []
        for person_db_id, pt_id in pt_pairs:
            pt_ids_map.setdefault(person_db_id, []).append(pt_id)
            pt_ids_flat.append(pt_id)

        seed_competencies(conn, persons, role_ids, level_ids)
        seed_leave_records(conn, persons, lt_ids)
        seed_week_logs(conn, persons, pt_ids_map)
        seed_resignations(conn, persons)
        seed_status_history(conn, persons)
        seed_employment_history(conn, persons)
        seed_skills(conn, persons, skill_ids)
        seed_job_requisitions(conn, role_ids, team_ids)
        seed_annual_reviews(conn, pt_ids_flat)

        print("\nSeed complete.")
        active = sum(1 for p in persons if not p["is_exited"])
        exited = sum(1 for p in persons if p["is_exited"])
        male = sum(1 for p in persons if p["gender"] == "M")
        female = sum(1 for p in persons if p["gender"] == "F")
        print(f"  Total people : 500 ({active} active, {exited} exited)")
        print(f"  Gender split : {male}M / {female}F")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed ERP database")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate all ERP tables first")
    parser.add_argument("--tables-only", action="store_true", help="Create tables only, no data")
    args = parser.parse_args()
    run(reset=args.reset, tables_only=args.tables_only)
