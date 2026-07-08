# HR Agent — Refactoring & Security Review Plan

## Current principles that must remain

Do not weaken these security guarantees:

- Identity must be resolved before any LLM call.
- Unregistered users must be rejected without invoking the model.
- Employees must never have direct SQL access.
- HR SQL queries must pass through SQL validation.
- Database access must remain read-only.
- All requests and tool usage must be audited.

## Phase 1: Analyze Current Architecture

First inspect the repository and provide:

- Current architecture diagram
- Existing modules and responsibilities
- Problems with current separation of concerns
- Places where LangGraph-specific code is mixed with business logic
- Security risks or missing controls

**Do not modify code yet.**

## Phase 2: Introduce Core Abstractions

Refactor toward this structure:

```
hr_agent/
├── core/
│   ├── context.py
│   ├── execution.py
│   └── registry.py
├── identity/
│   ├── resolver.py
│   └── roles.py
├── policies/
│   ├── permissions.py
│   └── evaluator.py
├── tools/
│   ├── employee/
│   ├── hr/
│   └── shared/
├── database/
│   ├── erp.py
│   └── app.py
├── security/
│   ├── sql_guard.py
│   └── output_filter.py
├── runtimes/
│   └── langgraph/
├── integrations/
│   └── slack.py
└── telemetry/
    └── audit.py
```

Keep the implementation simple. Do not over-engineer.

## Phase 3: Tool-First Architecture

Move business capabilities into tools.

Every tool should have:

- name
- description
- input schema
- output schema
- required permissions

Example:

```python
class EmployeeLeaveBalanceTool:
    permissions = ["employee.leave.read"]

    async def execute(self, context, employee_id):
        ...
```

- The agent should decide which tool to call.
- The tool itself must enforce authorization.
- **Never rely on prompts.**

## Phase 4: Add Permission Policy Layer

Replace scattered role checks.

Avoid:

```python
if user.role == "hr":
```

Create:

```python
policy.can(user, action="employee.salary.read")
```

Support these roles:

- `employee`
- `hr_manager`
- `admin`

Make permissions configurable.

## Phase 5: LangGraph Isolation

Move all LangGraph-specific code into `runtimes/langgraph/`.

The rest of the application should not import LangGraph.

The goal:

```
Business Logic
      |
Agent Runtime Interface
      |
LangGraph Implementation
```

Later we should be able to add another runtime without rewriting tools.

## Phase 6: Improve Agent Flow

Current flow:

```
Slack → Agent → Tools
```

Change to:

```
Slack
  |
Identity Resolution
  |
Rate Limit
  |
Create Agent Context
  |
Agent Runtime
  |
Tool Execution
  |
Security Validation
  |
Audit
  |
Response
```

## Phase 7: Add Agent Context Object

Create a shared context:

```python
AgentContext:
    user_id
    slack_id
    role
    permissions
    request_id
    metadata
```

Every tool receives this context.

## Phase 8: Add Better Observability

Audit each request with:

- user
- question
- selected tool
- execution time
- SQL generated
- rows returned
- errors
- model name

Do not store sensitive data unnecessarily.

## Phase 9: Testing

### Security

- unknown Slack user cannot call LLM
- employee cannot execute SQL
- employee cannot access another employee's data
- HR can execute allowed queries
- forbidden columns are blocked
- write queries are blocked

### Tools

- Each tool should have permission tests.

### Runtime

- LangGraph workflow should have integration tests.

## Phase 10: Keep It Simple

Do not add:

- unnecessary abstractions
- custom agent framework
- custom memory implementation
- custom workflow engine

Use LangGraph features where they provide value.

The goal is:

> "Thin application code + strong security boundaries + framework-provided agent orchestration."

After implementation, provide:

- Changed files
- Architecture diagram
- Security improvements
- Remaining technical debt
- Suggested next steps

---

# Security Audit

Perform a security audit of this HR Agent application.

Act as a principal security engineer reviewing an enterprise AI assistant.

## Check

- Can an employee access another employee's data?
- Can prompt injection bypass permissions?
- Can the LLM execute unsafe SQL?
- Can tools be called without authorization?
- Can Slack identity be spoofed?
- Can audit logs be modified?
- Can sensitive HR data leak through generated responses?

## Review

- authentication flow
- authorization flow
- tool permissions
- SQL guard
- database permissions
- output filtering
- logging

## For every issue provide

- severity
- exploit scenario
- affected file
- recommended fix
- test case to prevent regression

Do not only review prompts. Assume attackers know how LLM agents work.
