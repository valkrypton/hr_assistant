# HR ERP Database — Schema Context

This is the authoritative schema reference. Always use these descriptions, column
names, join patterns, and business rules when generating SQL. Never guess table or
column names — use only what is documented here.

---

## Table Overview

| Table | Purpose |
|-------|---------|
| `person` | Core employee record — one row per person |
| `department` | Organisational departments |
| `team` | Projects / sub-teams (each team = one project/client engagement) |
| `designation` | Job titles |
| `employment_type` | Employment classification |
| `person_team` | Person ↔ team assignment with dates and billable flag |
| `leave_record` | Individual leave requests |
| `leave_type` | Leave categories (annual, sick, etc.) |
| `leave_limit` | Allowed leave days per employment type |
| `person_leave_limit` | Per-person leave limit overrides |
| `holiday_record` | Public / company holidays |
| `person_week_log` | Weekly time-log header per person |
| `person_week_project` | Per-project breakdown within a weekly log |
| `competency_role` | Canonical role buckets (e.g. Software Engineer, QA Engineer) |
| `competency` | Individual competency dimensions assessed |
| `competency_level` | Levels within each competency (numeric `value`, human `name`) |
| `person_competency` | A person's competency assessment — role + overall level |
| `users_personresignation` | Resignation / separation records |
| `core_personstatushistory` | History of person status changes |
| `core_personemploymenthistory` | History of employment type + entity changes |
| `core_personemploymenttypehistory` | Additional employment type history |
| `job_requisition` | Open / closed job postings |
| `skill_category` | Skill / technology categories |
| `person_skill_category` | Skills assigned to a person |
| `peer_review` | Peer review scores |
| `annual_review_response` | Annual review responses per person+team assignment |

---

## Key Tables — Columns and Meanings

### `person`

| Column | Type | Meaning |
|--------|------|---------|
| `id` | int | Primary key |
| `full_name` | varchar | Employee display name — use this for name lookups |
| `person_id` | varchar | Human-readable employee ID (e.g. "EMP-001") |
| `joining_date` | date | Date joined the company (hire date) |
| `separation_date` | date | Last day; NULL if currently employed |
| `separation_type` | smallint | Separation reason: 2=Resignation, 3=Termination, 4=End of Contract |
| `is_active` | boolean | True = active record |
| `status_id` | int | Current employment status — see status IDs below |
| `department_id` | int FK→department | Person's department |
| `current_designation_id` | int FK→designation | Current job title |
| `employment_type_id` | int FK→employment_type | Employment classification |
| `experience_start_date` | date | Career start date — use for years-of-experience calculations |

**NEVER expose in any response:** `date_of_birth`, `nic`, `nic_expiry`,
`cellphone_number`, `current_address`, `permanent_address`, `personal_email`,
`previous_salary`, `emergency_contact_number`, `emergency_contact_name`.

### Status IDs (hardcode in WHERE clauses — `status` table is not queryable)

| `status_id` | Meaning |
|-------------|---------|
| 10 | Active |
| 22 | Active-B (Active on Bench) |
| 17 | Probation |
| 11 | Resigned |
| 12 | Terminated |
| 14 | Laid off |
| 20 | End of contract |
| 13 | Inactive |
| 28 | Withdrawn |

- **Active / currently employed:** `person.is_active = true` OR `status_id IN (10, 22, 17)`
- **Exited:** `status_id IN (11, 12, 14, 20)` OR `separation_date IS NOT NULL`

### `employment_type`

| `type` value | Name | Classification |
|-------------|------|---------------|
| 1 | Employee | **employed** |
| 4 | Intern | **employed** |
| 5 | EOR | **employed** |
| 2 | Contract | **subcontractor** |
| 3 | Sub-contractor | **subcontractor** |

Join: `person JOIN employment_type ON person.employment_type_id = employment_type.id`

### `team`

| Column | Meaning |
|--------|---------|
| `id` | Primary key |
| `name` | Team / project name |
| `billable` | True = client-billable project |
| `is_active` | True = project is active |
| `lead_id` | FK → person (team lead) |
| `description` | Free-text project description |

### `person_team`

One row per assignment period (a person can be on multiple teams simultaneously).

| Column | Meaning |
|--------|---------|
| `person_id` | FK → person |
| `nsubteam_id` | FK → team |
| `start_date` | Assignment start date |
| `end_date` | Assignment end date; **NULL = currently active assignment** |
| `billable` | True = this assignment is billable to a client |
| `is_active` | True = active assignment record |
| `is_overall_lead` | True = this person leads this team |

**Current assignment:** `end_date IS NULL AND is_active = true`

### `leave_record`

| Column | Meaning |
|--------|---------|
| `person_id` | FK → person |
| `leave_type_id` | FK → leave_type |
| `start` | Leave start date |
| `end` | Leave end date (inclusive) |
| `status` | 0=Pending, 1=Approved, 2=Rejected |
| `half_day` | True = half-day leave |

**Approved leave:** `leave_record.status = 1`

### `person_week_log`

Weekly time-log header — one row per person per week.

| Column | Meaning |
|--------|---------|
| `person_id` | FK → person |
| `week_starting` | Monday of the logged week (ISO date) |
| `hours` | Total hours logged |
| `minutes` | Additional minutes — total decimal hours = `hours + minutes / 60.0` |
| `is_completed` | True = log submitted; False = missing / incomplete |

**Log compliance check:** look for weeks where `is_completed = false`, or where no
`person_week_log` row exists at all for a person + week combination.

### `person_week_project`

Per-project breakdown of hours within a weekly log.

| Column | Meaning |
|--------|---------|
| `person_week_log_id` | FK → person_week_log |
| `person_team_id` | FK → person_team (identifies which project) |
| `hours` / `minutes` | Hours logged on this project this week |
| `is_approved` | True = this entry is approved by reviewer |

### `person_competency`

| Column | Meaning |
|--------|---------|
| `person_id` | FK → person |
| `role_id` | FK → competency_role |
| `overall_level_id` | FK → competency_level (overall assessed level) |
| `status` | 0=Draft, 1=In Review, 2=Approved |
| `is_enabled` | True = record is active |

**Current approved competency:** `status = 2 AND is_enabled = true`

### `competency_role`

| Column | Meaning |
|--------|---------|
| `name` | Role name (e.g. "Software Engineer", "QA Engineer", "Product Manager") |
| `value` | Numeric priority / seniority indicator |
| `is_active` | True = role is in use |

### `competency_level`

| Column | Meaning |
|--------|---------|
| `name` | Level name (e.g. "Junior", "Mid", "Senior", "Lead") |
| `value` | Numeric level — higher = more senior |

### Resignations, Terminations, and Exits — `users_personresignation`

Use this table for any query about resigned employees, terminations, attrition, how many people left, separation records, or exit counts. **`separation_type` lives here, NOT on the `person` table.** This is the only authoritative source for exit data.

| Column | Meaning |
|--------|---------|
| `person_id` | FK → person |
| `resignation_date` | Date resignation was submitted |
| `last_working_day` | Last day at the company — use this for exit-year filtering |
| `separation_type` | 2=Resignation, 3=Termination, 4=End of Contract |
| `status` | 0=Pending, 1=Approved, 2=Revoked — **always filter `status = 1` (Approved)** |

**Canonical query patterns:**
```sql
-- Count resignations in a given year
SELECT COUNT(*) FROM users_personresignation
WHERE separation_type = 2 AND status = 1
  AND EXTRACT(YEAR FROM last_working_day) = <year>;

-- Count terminations in a given year
SELECT COUNT(*) FROM users_personresignation
WHERE separation_type = 3 AND status = 1
  AND EXTRACT(YEAR FROM last_working_day) = <year>;

-- Resignations by department
SELECT d.name, COUNT(*) FROM users_personresignation upr
JOIN person p ON upr.person_id = p.id
JOIN department d ON p.ndepartment_id = d.id
WHERE upr.separation_type = 2 AND upr.status = 1
GROUP BY d.name;
```

### `core_personstatushistory`

Tracks all status transitions over time.

| Column | Meaning |
|--------|---------|
| `person_id` | FK → person |
| `status_id` | The status during this period (use status ID table above) |
| `start_date` | When this status became effective |
| `end_date` | When this status ended; NULL = currently in this status |

---

## Business Rules

### Employment Classification
- **"employed"** = `employment_type.type IN (1, 4, 5)` (Employee, Intern, EOR)
- **"subcontractor"** = `employment_type.type IN (2, 3)` (Contract, Sub-contractor)

### Active vs Exited Employees — resignations, terminations, attrition, how many resigned, who left
- Use `person.is_active = true` to filter active employees.
- Use `person.separation_date IS NOT NULL` to identify exited employees.
- **All resignation/termination counts must use `users_personresignation`** — `separation_type` is NOT a column on `person`.
- Resignations: `WHERE separation_type = 2 AND status = 1` (status=1 = Approved; always required).
- Terminations: `WHERE separation_type = 3 AND status = 1`.
- Filter by year: `EXTRACT(YEAR FROM last_working_day) = <year>` — use `last_working_day`, not `resignation_date`.
- Example — resignations in 2025: `SELECT COUNT(*) FROM users_personresignation WHERE separation_type = 2 AND status = 1 AND EXTRACT(YEAR FROM last_working_day) = 2025`

### Bench / Non-Billable
- A person is on bench when their current `person_team` assignment (`end_date IS NULL AND is_active = true`) has `billable = false`.
- Long-term bench: `pt.start_date <= CURRENT_DATE - INTERVAL 'N days'` on a non-billable active assignment.

### Log Compliance
- Missing log = no `person_week_log` row for a given `week_starting`, OR `is_completed = false`.
- Always exclude employees on approved leave (`leave_record.status = 1`) when checking compliance.
- Use `holiday_record` to also exclude company holidays.

### Utilisation
- Utilisation % = (billable hours / total logged hours) × 100 over a period.
- Billable hours: `person_week_project` JOIN `person_team WHERE billable = true`.
- Total hours: `person_week_log.hours + person_week_log.minutes / 60.0`.

### Competency / Performance
- A person's role and level: `person_competency (status=2, is_enabled=true)` → `competency_role` → `competency_level`.
- **There is no `hr_records` table.** For queries about warnings or performance issues, use
  `core_personstatushistory` (status transitions) and `person_week_log` compliance gaps as
  operational proxies. Always state in the response that direct HR warning records are unavailable
  and results are proxy indicators only.

### New Joiners
- Count employees hired in a given year: `WHERE EXTRACT(YEAR FROM person.joining_date) = <year>`.
- Break down by classification: join `employment_type` and group by `employment_type.type`.

### Attrition / Resignations
- All exit data lives in `users_personresignation` — always join on `person_id`.
- `separation_type` is a column on `users_personresignation`, **not** on `person`.
- Always include `AND status = 1` (Approved) to exclude pending/revoked records.
- Filter by year using `EXTRACT(YEAR FROM last_working_day)` — not `separation_date` or `resignation_date`.
- For same-year joiners who also left: compare `EXTRACT(YEAR FROM person.joining_date)` with `EXTRACT(YEAR FROM upr.last_working_day)`.
- Years of experience at exit: `upr.last_working_day - person.joining_date`.
