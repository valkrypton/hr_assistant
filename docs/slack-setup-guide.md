# HR Assistant — Slack Setup Guide

This guide walks a Slack workspace admin through connecting HR Assistant to your workspace and setting up the `#ask-hr` channel.

---

## Prerequisites

Before starting, make sure:
- The HR Assistant server is running and reachable over the internet (via a public URL or ngrok for local testing)
- You have admin access to both the Slack workspace and the HR Assistant admin panel

---

## Step 1 — Create the Slack App

1. Go to **https://api.slack.com/apps** and sign in with your workspace admin account
2. Click **"Create New App"** → **"From scratch"**
3. Name it **HR Assistant** and select your workspace → **Create App**

---

## Step 2 — Configure Bot Permissions

1. In the left sidebar click **OAuth & Permissions**
2. Scroll to **Bot Token Scopes** and add all of the following:

   | Scope | Purpose |
   |-------|---------|
   | `app_mentions:read` | Receive @HR Assistant mentions in channels |
   | `chat:write` | Post replies |
   | `im:history` | Read direct messages sent to the bot |
   | `im:read` | Access DM channel info |
   | `im:write` | Open DM conversations |
   | `channels:history` | Read messages in public channels the bot is in |

3. Scroll to the top of the same page and click **Install to Workspace** → **Allow**
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`) — you will need this later

---

## Step 3 — Get the Signing Secret

1. In the left sidebar click **Basic Information**
2. Scroll to **App Credentials**
3. Copy the **Signing Secret**

---

## Step 4 — Configure the Server

Add both values to the server's `.env` file:

```
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_SIGNING_SECRET=your-signing-secret-here
```

Restart the server after saving.

---

## Step 5 — Enable Event Subscriptions

1. In the left sidebar click **Event Subscriptions**
2. Toggle **Enable Events** on
3. Set the **Request URL** to:
   ```
   https://your-server-url/webhook/slack
   ```
   Slack will immediately send a verification ping — wait for the green **Verified** checkmark before continuing
4. Scroll to **Subscribe to Bot Events** and add:
   - `app_mention`
   - `message.im`
5. Click **Save Changes**
6. A yellow banner will appear at the top — click **Reinstall your app** and approve it

---

## Step 6 — Enable Direct Messages

1. In the left sidebar click **App Home**
2. Scroll to **Show Tabs**
3. Toggle **Messages Tab** on
4. Check the box: **"Allow users to send Slash commands and messages from the messages tab"**
5. Click **Save**
6. Reinstall the app again via **OAuth & Permissions** → **Reinstall to Workspace**

> **If DMs still show "Sending messages to this app has been turned off":**
> This is a workspace-level policy, not an app setting. A workspace admin needs to allow it:
> 1. Go to **https://[your-workspace].slack.com/admin/settings**
> 2. Search for **"Allow members to send messages to apps"** and enable it
> 3. Save changes — no reinstall needed

---

## Step 7 — Create the #ask-hr Channel

1. In Slack, create a new channel named **#ask-hr**
2. Set the description to: *"Ask the HR Assistant any workforce question — type @HR Assistant followed by your question"*
3. Invite the bot to the channel by typing in the channel:
   ```
   /invite @HR Assistant
   ```
4. Post a welcome message so users know how to use it:
   ```
   Welcome to #ask-hr! Ask @HR Assistant any workforce question.
   
   Example questions:
   • @HR Assistant How many employees do we have?
   • @HR Assistant Who is on the bench right now?
   • @HR Assistant Show me the backend team roster
   ```

---

## Step 8 — Register Users

Every person who will use HR Assistant must be registered with a role. This controls what data they are allowed to see.

**Option A — Admin Panel (recommended)**
1. Go to **http://your-server/admin**
2. Click **HR Users** → **Create**
3. Fill in:
   - **Employee ID** — the person's ID in the ERP system
   - **Role** — their access level (see table below)
   - **Slack User ID** — found in Slack by clicking their profile → three-dot menu → **Copy member ID** (starts with `U`)
   - **Department ID** — required for Department Head role
   - **Team ID** — required for Team Lead role

**Option B — API**
```bash
curl -X POST https://your-server/admin/users \
  -H "Content-Type: application/json" \
  -d '{
    "employee_id": 42,
    "role": "hr_manager",
    "slack_user_id": "U07A2RVUMBJ"
  }'
```

**Role reference:**

| Role value | Who gets it | What they see |
|------------|-------------|---------------|
| `cto_ceo` | CEO, CTO | Full company-wide access |
| `hr_manager` | HR team members | Full company-wide access |
| `dept_head` | Department heads | Their department only |
| `team_lead` | Team leads | Their team only |

> Users who send a message without being registered will get no response. Register them first.

---

## Step 9 — Verify Everything Works

1. In `#ask-hr`, type:
   ```
   @HR Assistant how many employees do we have?
   ```
2. HR Assistant should reply in the thread within 15 seconds
3. Check **http://your-server/admin** → **Audit Logs** to confirm the query was recorded

---

## Ongoing Administration

**Adding a new user:** Follow Step 8 above
**Removing a user:** Go to the admin panel → HR Users → find the user → set **Is Active** to off
**Viewing query history:** Go to **http://your-server/admin** → **Audit Logs**
**Checking server health:** Visit **http://your-server/health** — should return `{"status": "ok"}`
