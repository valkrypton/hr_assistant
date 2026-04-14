# HR ERP Database — Schema Reference

Always use the exact column names, join patterns, and business rules documented
here. Never guess — if a column or table is not listed, it does not exist or is
not queryable.

---

## Table Overview

| Table | Purpose |
|-------|---------|
| `person` | Core employee record — one row per person |
| `department` | Organisational departments |
| `team` | Projects / sub-teams (one team = one client/project engagement) |
| `designation` | Job titles |
| `employment_type` | Employment classification lookup |
| `person_team` | Person ↔ team assignment history with billable flag |
| `leave_record` | Individual leave requests |
| `leave_type` | Leave categories (annual, sick, etc.) |
| `leave_limit` | Allowed leave days by employment type + experience bracket |
| `person_leave_limit` | Per-person leave limit overrides |
| `holiday_record` | Public / company holidays |
| `person_week_log` | Weekly time-log header per person |
| `person_week_project` | Per-project hour breakdown within a weekly log |
| `competency_role` | Canonical role buckets (e.g. Software Engineer, QA Engineer) |
| `competency` | Individual competency dimensions |
| `competency_level` | Seniority levels (Junior → Lead) |
| `person_competency` | A person's assessed competency role + level |
| `users_personresignation` | Resignation / separation records with approval workflow |
| `core_personstatushistory` | Full history of person status transitions |
| `core_personemploymenthistory` | History of employment type + entity changes |
| `core_personemploymenttypehistory` | Employment type change log |
| `job_requisition` | Open / closed hiring requests |
| `skill_category` | Skill / technology category definitions |
| `person_skill_category` | Skills assigned to a person |
| `annual_review_response` | Annual review scores per person-team assignment |

---

## Table Definitions

### `person`

One row per employee. The central table joined by almost everything.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `full_name` | varchar(100) | Display name — **always use for name lookups, not first/last name** |
| `person_id` | varchar(20) | Human-readable ID (e.g. "EMP-001") |
| `joining_date` | date | Hire date — use for tenure, new-joiner queries |
| `career_start_date` | date | Start of entire career — use for total years-of-experience |
| `experience_start_date` | date | Alternative YoE anchor (may differ from career_start_date) |
| `confirmation_date` | date | Probation end / confirmation date |
| `separation_date` | date | Last day at company; **NULL = currently employed** |
| `separation_type` | smallint | Denormalised exit reason: 2=Resignation, 3=Termination, 4=End of Contract |
| `separation_reason` | text | Free-text reason for separation |
| `is_active` | boolean | True = active employee record |
| `is_loggable` | boolean | **True = must submit weekly time logs** — always filter on this for log compliance |
| `is_remote` | boolean | True = remote employee |
| `status_id` | int FK→status | Current employment status (see Status IDs below) |
| `department_id` | int FK→department | Employee's department |
| `current_designation_id` | int FK→designation | Current job title |
| `employment_type_id` | int FK→employment_type | Employment classification |
| `notice_period_duration` | smallint | Notice period in days |
| `entity_id` | int | Legal entity the person belongs to |

> **Note:** `person.separation_type` is a denormalised column. For accurate
> exit counts with approval workflow, use `users_personresignation` instead —
> it has `status` (Approved/Pending/Revoked) and `last_working_day`.

### `department`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `name` | varchar(30) | Department name |
| `is_active` | boolean | True = active department |

Join: `person JOIN department ON person.department_id = department.id`

### `team`

One team = one project or client engagement.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `name` | varchar(100) | Team / project name |
| `complete_name` | text | Full hierarchical name |
| `description` | text | Free-text project description |
| `billable` | boolean | True = client-billable project |
| `is_active` | boolean | True = project is active |
| `lead_id` | int FK→person | Team lead |
| `start_date` | date | Project start |
| `end_date` | date | Project end; NULL = ongoing |
| `is_division` | boolean | True = this is a division-level node |

### `designation`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `name` | varchar(100) | Job title (e.g. "Senior Software Engineer") |
| `core_name` | varchar(100) | Canonical / normalised title |
| `is_active` | boolean | |

Join: `person JOIN designation ON person.current_designation_id = designation.id`

### `employment_type`

| `type` value | `name` | Classification |
|-------------|--------|---------------|
| 1 | Employee | **employed** |
| 4 | Intern | **employed** |
| 5 | EOR | **employed** |
| 2 | Contract | **subcontractor** |
| 3 | Sub-contractor | **subcontractor** |

Join: `person JOIN employment_type ON person.employment_type_id = employment_type.id`

### `person_team`

One row per assignment period. A person can have multiple simultaneous assignments.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `person_id` | int FK→person | |
| `nsubteam_id` | int FK→team | **The team FK — note the non-obvious name** |
| `start_date` | date | Assignment start |
| `end_date` | date | Assignment end; **NULL = currently active** |
| `billable` | boolean | True = this assignment is billable |
| `is_active` | boolean | True = active record |
| `is_overall_lead` | boolean | True = person is leading this team |
| `primary` | boolean | True = this is the person's primary assignment |

**Current active assignment:** `end_date IS NULL AND is_active = true`

Join to team: `person_team JOIN team ON person_team.nsubteam_id = team.id`

### `leave_record`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `person_id` | int FK→person | |
| `leave_type_id` | int FK→leave_type | |
| `start` | date | Leave start (inclusive) |
| `end` | date | Leave end (inclusive) |
| `status` | smallint | 0=Pending, 1=Approved, 2=Rejected |
| `half_day` | boolean | True = half-day leave |
| `taken` | boolean | True = leave has been taken |

**Approved leave:** `leave_record.status = 1`

### `holiday_record`

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `reason` | varchar(250) | Holiday name |
| `start` | date | Start date (inclusive) |
| `end` | date | End date (inclusive) |
| `is_active` | boolean | |

### `person_week_log`

Weekly time-log header — one row per person per week.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `person_id` | int FK→person | |
| `week_starting` | date | Monday of the logged week |
| `hours` | int | Whole hours logged |
| `minutes` | int | Additional minutes; total = `hours + minutes / 60.0` |
| `is_completed` | boolean | True = log submitted; False = missing / incomplete |

**Log compliance:** `is_completed = false` OR no row exists for a given person + week.
**Only check `is_loggable = true` employees** — others are exempt from logging.

### `person_week_project`

Per-project hour breakdown within a weekly log.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `person_week_log_id` | int FK→person_week_log | |
| `person_team_id` | int FK→person_team | Identifies which project |
| `hours` / `minutes` | int | Hours on this project this week |
| `is_approved` | boolean | True = entry approved by reviewer |

### `person_competency`

A person's assessed competency — role bucket + seniority level.

| Column | Type | Notes |
|--------|------|-------|
| `person_id` | int FK→person | |
| `role_id` | int FK→competency_role | The role bucket (e.g. Software Engineer) |
| `overall_level_id` | int FK→competency_level | Assessed seniority level |
| `status` | smallint | 0=Draft, 1=In Review, **2=Approved** |
| `is_enabled` | boolean | True = active/current assessment |

**Current approved competency:** `status = 2 AND is_enabled = true`

### `competency_role`

| Column | Notes |
|--------|-------|
| `id` | Primary key |
| `name` | Role bucket name (e.g. "Software Engineer", "QA Engineer", "Product Manager", "DevOps Engineer") |
| `is_active` | True = role is in use |

### `competency_level`

| Column | Notes |
|--------|-------|
| `id` | Primary key |
| `name` | Level name (e.g. "Junior", "Mid", "Senior", "Lead") |
| `value` | Numeric level — higher = more senior |

### `users_personresignation` — canonical exit records

**Use this table for all resignation / termination / exit queries.** It has the
full approval workflow and accurate `last_working_day`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | Primary key |
| `person_id` | int FK→person | |
| `separation_type` | smallint | **2=Resignation, 3=Termination, 4=End of Contract** |
| `status` | smallint | 0=Pending, **1=Approved**, 2=Revoked — **always filter `status = 1`** |
| `resignation_date` | date | Date resignation was submitted |
| `last_working_day` | date | **Last day at company — use this for exit-year filtering** |
| `resignation_reason_type` | smallint | Categorised reason |
| `resignation_reason` | varchar | Free-text reason |

**Canonical exit queries:**
```sql
-- Resignations in a year
SELECT COUNT(*) FROM users_personresignation
WHERE separation_type = 2 AND status = 1
  AND EXTRACT(YEAR FROM last_working_day) = <year>;

-- Terminations in a year
SELECT COUNT(*) FROM users_personresignation
WHERE separation_type = 3 AND status = 1
  AND EXTRACT(YEAR FROM last_working_day) = <year>;

-- Resignations by department
SELECT d.name, COUNT(*) AS count
FROM users_personresignation upr
JOIN person p ON upr.person_id = p.id
JOIN department d ON p.department_id = d.id
WHERE upr.separation_type = 2 AND upr.status = 1
GROUP BY d.name ORDER BY count DESC;

-- Resignations by years-of-experience bracket (1-year intervals)
SELECT
  FLOOR(EXTRACT(EPOCH FROM (upr.last_working_day - p.joining_date)) / 86400 / 365) AS yoe_bracket,
  COUNT(*) AS count
FROM users_personresignation upr
JOIN person p ON upr.person_id = p.id
WHERE upr.separation_type = 2 AND upr.status = 1
GROUP BY yoe_bracket ORDER BY yoe_bracket;

-- Same-year joiners who also left (cohort attrition)
SELECT COUNT(*) FROM users_personresignation upr
JOIN person p ON upr.person_id = p.id
WHERE upr.status = 1
  AND EXTRACT(YEAR FROM p.joining_date) = EXTRACT(YEAR FROM upr.last_working_day);
```

### `core_personstatushistory`

Full history of status transitions per person.

| Column | Type | Notes |
|--------|------|-------|
| `person_id` | int FK→person | |
| `status_id` | int | Status during this period (see Status IDs) |
| `start_date` | date | When this status became effective |
| `end_date` | date | When it ended; NULL = current status |

Use for: tracking when someone moved to Probation, Inactive, or Resigned status.

### `core_personemploymenthistory`

History of employment type + legal entity changes.

| Column | Type | Notes |
|--------|------|-------|
| `person_id` | int FK→person | |
| `employment_type_id` | int FK→employment_type | |
| `entity_id` | int | Legal entity |
| `status_id` | int | Status during this period |
| `start_date` | date | |
| `end_date` | date | NULL = current |

### `skill_category` + `person_skill_category`

Skills and technologies assigned to employees.

`skill_category`: `id`, `title` (skill/technology name), `is_active`

`person_skill_category`: `person_id` FK→person, `skill_category_id` FK→skill_category, `skill_title` (free-text label)

Join: `person JOIN person_skill_category ON person.id = person_skill_category.person_id`

### `job_requisition`

Open / closed hiring requests.

| Column | Type | Notes |
|--------|------|-------|
| `role_id` | int FK→competency_role | Role being hired for |
| `status` | smallint | 1=Open, 2=Closed (typical values) |
| `min_experience` / `max_experience` | smallint | Years of experience required |
| `tech_stack` | text | Technologies required |
| `required_at` | date | When the resource is needed |
| `subteam_id` | int FK→team | Team requesting the hire |

### `annual_review_response`

Annual review scores per person-team assignment.

| Column | Notes |
|--------|-------|
| `person_team_id` | FK → person_team |
| `skill_rate` | Numeric skill rating |

---

## Status IDs

`status` table is not queryable — hardcode these values in WHERE clauses.

| `status_id` | Meaning |
|-------------|---------|
| 10 | Active |
| 22 | Active-B (Active, on Bench) |
| 17 | Probation |
| 11 | Resigned |
| 12 | Terminated |
| 14 | Laid off |
| 20 | End of contract |
| 13 | Inactive |
| 28 | Withdrawn |

- **Currently employed (active):** `person.is_active = true` OR `status_id IN (10, 22, 17)`
- **Exited:** `status_id IN (11, 12, 14, 20)` OR `separation_date IS NOT NULL`

---

## Common Join Patterns

```sql
-- Person → Department
JOIN department ON person.department_id = department.id

-- Person → Designation (job title)
JOIN designation ON person.current_designation_id = designation.id

-- Person → Employment type
JOIN employment_type ON person.employment_type_id = employment_type.id

-- Person → Current team assignment
JOIN person_team pt ON pt.person_id = person.id
  AND pt.end_date IS NULL AND pt.is_active = true
JOIN team ON pt.nsubteam_id = team.id

-- Person → Current competency role + level
JOIN person_competency pc ON pc.person_id = person.id
  AND pc.status = 2 AND pc.is_enabled = true
JOIN competency_role cr ON pc.role_id = cr.id
JOIN competency_level cl ON pc.overall_level_id = cl.id

-- Person → Skills
JOIN person_skill_category psc ON psc.person_id = person.id
JOIN skill_category sc ON psc.skill_category_id = sc.id

-- Person → Exit record
JOIN users_personresignation upr ON upr.person_id = person.id
  AND upr.status = 1

-- Weekly log → Project breakdown
JOIN person_week_project pwp ON pwp.person_week_log_id = person_week_log.id
JOIN person_team pt ON pwp.person_team_id = pt.id
JOIN team ON pt.nsubteam_id = team.id
```

---

## Business Rules & Canonical Query Patterns

### Employment Classification
- **"employed"** = `employment_type.type IN (1, 4, 5)` — Employee, Intern, EOR
- **"subcontractor"** = `employment_type.type IN (2, 3)` — Contract, Sub-contractor

### Active Employees
```sql
-- Active headcount
SELECT COUNT(*) FROM person WHERE is_active = true;

-- Active by department
SELECT d.name, COUNT(*) FROM person p
JOIN department d ON p.department_id = d.id
WHERE p.is_active = true GROUP BY d.name;
```

### New Joiners
```sql
-- Count by year
SELECT COUNT(*) FROM person
WHERE EXTRACT(YEAR FROM joining_date) = <year>;

-- Break down employed vs subcontractor
SELECT
  CASE WHEN et.type IN (1,4,5) THEN 'employed' ELSE 'subcontractor' END AS classification,
  COUNT(*) AS count
FROM person p
JOIN employment_type et ON p.employment_type_id = et.id
WHERE EXTRACT(YEAR FROM p.joining_date) = <year>
GROUP BY classification;
```

### Attrition / Exits
- Always use `users_personresignation` — it has the approval `status` column.
- Filter `status = 1` (Approved) to exclude pending/revoked records.
- Use `last_working_day` for exit-year filtering, not `resignation_date`.
- `person.separation_type` exists but is a denormalised copy — prefer `users_personresignation.separation_type`.

### Log Compliance
```sql
-- Employees missing logs for a specific week (excluding non-loggable and approved leave)
SELECT p.full_name, d.name AS department
FROM person p
JOIN department d ON p.department_id = d.id
WHERE p.is_active = true
  AND p.is_loggable = true
  AND NOT EXISTS (
    SELECT 1 FROM person_week_log pwl
    WHERE pwl.person_id = p.id
      AND pwl.week_starting = '<week_monday_date>'
      AND pwl.is_completed = true
  )
  AND NOT EXISTS (
    SELECT 1 FROM leave_record lr
    WHERE lr.person_id = p.id
      AND lr.status = 1
      AND lr.start <= '<week_monday_date>'::date + 4
      AND lr.end >= '<week_monday_date>'::date
  );
```
**Always filter `is_loggable = true`** — employees with `is_loggable = false` are exempt.

### Bench / Non-Billable
```sql
-- Currently on bench (active but non-billable assignment)
SELECT p.full_name, d.name AS department, pt.start_date AS bench_since
FROM person p
JOIN department d ON p.department_id = d.id
JOIN person_team pt ON pt.person_id = p.id
  AND pt.end_date IS NULL AND pt.is_active = true AND pt.billable = false
WHERE p.is_active = true
ORDER BY pt.start_date;

-- Bench > N days
WHERE pt.start_date <= CURRENT_DATE - INTERVAL '<N> days'
```

### Competency / Role Headcount
```sql
-- Headcount by competency role
SELECT cr.name AS role, COUNT(*) AS count
FROM person p
JOIN person_competency pc ON pc.person_id = p.id
  AND pc.status = 2 AND pc.is_enabled = true
JOIN competency_role cr ON pc.role_id = cr.id
WHERE p.is_active = true
GROUP BY cr.name ORDER BY count DESC;
```

### Years of Experience
- For **total career YoE**: use `person.career_start_date`.
- For **tenure at company**: `CURRENT_DATE - person.joining_date` (or `last_working_day - joining_date` for exited employees).
- Age in years from a date: `EXTRACT(EPOCH FROM (end_date - start_date)) / 86400 / 365`

### No `hr_records` Table
There is no `hr_records` table. For warnings / disciplinary queries use proxies:
- `core_personstatushistory` — status transitions (e.g. move to Inactive/Probation = flag)
- `person_week_log` — compliance gaps (`is_completed = false` patterns)

Always state in the response that direct HR warning records are unavailable.
