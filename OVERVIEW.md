# HR Assistant — Product Overview

## What is it?

HR Assistant is an AI-powered tool that lets you ask questions about your workforce in plain English and get instant, accurate answers — without writing reports, waiting for an analyst, or digging through spreadsheets.

You ask a question. It answers.

> *"How many people joined this year?"*
> → "There were 36 new joiners in 2025, of which 28 were full-time employees and 8 were contractors."

---

## How do you use it?

There are two ways to talk to HR Assistant:

**1. Slack**
Mention `@HR Assistant` in any channel, or send it a direct message. It replies in the same thread within seconds.

**2. Web browser**
Open the HR Assistant web page and type your question directly. No login required for internal use.

---

## What can it answer?

HR Assistant can answer any workforce question that your company data supports. Some examples:

**Headcount & joiners**
- How many employees do we have right now?
- How many people joined in 2025? How many were contractors vs full-time?

**Attrition & exits**
- How many people resigned this year?
- Which department had the most attrition?
- How many people who joined in 2024 also left in 2024?

**Availability & bench**
- Who has been non-billable for more than 30 days?
- Who is available for a new project starting next month?

**Teams & rosters**
- Show me everyone on the backend team right now.
- Who is on leave next week?

**Compliance**
- Who hasn't submitted their daily logs this week?
- Who is logging less than 6 hours a day on average?

**Individual lookups**
- What is Ali's current project and utilisation?
- What skills does the design team have?

---

## Who sees what?

Not everyone gets the same view. HR Assistant automatically shows each person only the data they are allowed to see based on their role.

| Role | What they can see |
|------|------------------|
| **CTO / CEO** | Everything — full company-wide access |
| **HR Manager** | Everything — employee details, leaves, warnings, attrition |
| **Department Head** | Their own department only |
| **Team Lead** | Their own team only |

This happens automatically. A Team Lead asking "show me the full roster" will only see their own team — they will never see data from other teams, and the system will not tell them that data was hidden.

Certain information is **never shown to anyone**, regardless of role: salary figures, bank details, personal phone numbers, home addresses, and medical records.

---

## Where does the data come from?

HR Assistant reads directly from the company ERP — the same system that stores employee records, attendance, leaves, projects, and billing status. The data is always live; there is no separate copy to keep in sync.

The system can only **read** data. It cannot create, edit, or delete anything in the ERP.

---

## Is there an admin panel?

Yes. Admins can access a management panel at `http://your-server/admin` to:

- **Register users** — map an employee to their Slack account and assign them a role
- **View the audit log** — see a full history of every question asked, who asked it, and which data was accessed

---

## Audit trail

Every question asked is logged automatically with:
- Who asked it
- When they asked it
- What they asked
- What data the system accessed to answer it

This log cannot be edited or deleted, providing a permanent compliance record.

---

## Supported AI models

HR Assistant works with several AI providers. The model can be swapped at any time without changing anything else:

- OpenAI (GPT-4o)
- Anthropic (Claude)
- xAI (Grok)
- Alibaba (Qwen)
- Ollama (local, self-hosted)

---

## What it is not

- It does not replace HR judgment — it surfaces data, it does not make decisions.
- It does not send emails, create tasks, or modify any records.
- It does not have memory between conversations — each question is independent.
