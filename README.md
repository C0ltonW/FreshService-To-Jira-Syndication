# FreshService-to-Jira Syndication Framework

## Overview

The FreshService-to-Jira Syndication Framework synchronizes Freshservice tickets into Jira while maintaining Freshservice as the system of record.

Version 2.0 introduces a fully configuration-driven multi-department architecture that allows new departments, projects, routing rules, and status mappings to be onboarded without code changes.

### Core Capabilities

- Create Jira issues from Freshservice tickets
- Update existing Jira issues idempotently
- Synchronize Freshservice conversations as Jira comments
- Synchronize attachments
- Freshservice → Jira status alignment
- Jira → Freshservice reverse status synchronization
- Department-based routing and ownership
- Subcategory-to-Jira-label automation
- Dry-run and live test modes
- Configuration-only onboarding of new departments

---

## At a Glance

```text
                    ┌─────────────────────────┐
                    │    departments.json     │
                    │                         │
                    │  • Agent Ownership      │
                    │  • Jira Projects        │
                    │  • Assignment Rules     │
                    │  • Status Mappings      │
                    └───────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │   Department Registry   │
                    │                         │
                    │ Loads & Validates       │
                    │ Department Config       │
                    └───────────┬─────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                         Sync Engine                         │
│                                                             │
│  • Create / Update Jira Issues                             │
│  • Sync Comments                                            │
│  • Sync Attachments                                         │
│  • Apply Assignment Routing                                 │
│  • Add Subcategory Labels                                   │
│  • Freshservice → Jira Status Sync                          │
│  • Jira → Freshservice Status Sync                          │
│                                                             │
└───────────────┬─────────────────────────────┬───────────────┘
                │                             │
                ▼                             ▼

      ┌─────────────────┐         ┌─────────────────┐
      │  Freshservice   │◄──────► │      Jira       │
      │                 │         │                 │
      │ Source of Truth │         │ Department      │
      │ for Tickets     │         │ Work Tracking   │
      └─────────────────┘         └─────────────────┘


              Add New Department?
                        │
                        ▼

          Update departments.json Only

                        │

                        ▼

                No Code Changes
```

## Architecture

### Multi-Department Design

The framework is designed around department configurations defined in:

```text
settings/departments.json
```

All department-specific behavior is configuration-driven:

- Freshservice agent ownership
- Jira project destination
- Jira board destination
- Assignment routing
- Status synchronization
- Category/subcategory mappings

### Key Principle

> Adding a department should be a configuration change, not a code change.

No department-specific conditional logic exists in the codebase.

Examples intentionally avoided:

```python
if department == "fpna":
```

```python
if department == "wms":
```

```python
match department:
```

Instead, departments are loaded dynamically through the Department Registry and processed through a generic synchronization loop.

### Synchronization Flow

```text
Freshservice
    |
    v
Department Registry
    |
    v
Sync Engine
    |
    +--> Create / Update Jira Issue
    +--> Sync Comments
    +--> Sync Attachments
    +--> Sync Statuses
    +--> Update Labels
    |
    v
Jira
```

---

## Department Configuration

Departments are defined entirely through JSON.

Example:

```json
{
  "dept_id": "it",
  "name": "IT Support",
  "agent_ids": [11111, 22222],
  "jira_project_key": "IT",
  "jira_issue_type_id": "10001",
  "jira_board_id": 456,
  "enabled": true,
  "sync_comments": true,
  "sync_attachments": true,
  "assignment_map": {},
  "status_sync_map": {}
}
```

### Department Configuration Fields

| Field | Purpose |
|---------|---------|
| dept_id | Unique department identifier |
| name | Display name |
| agent_ids | Freshservice agents belonging to department |
| jira_project_key | Target Jira project |
| jira_issue_type_id | Jira issue type |
| jira_board_id | Jira board |
| enabled | Enables synchronization |
| sync_comments | Synchronize comments |
| sync_attachments | Synchronize attachments |
| assignment_map | Category/subcategory routing |
| status_sync_map | Jira → Freshservice status mappings |

---

## How New Departments Are Added

Adding a department requires only:

1. Open `settings/departments.json`
2. Add a department configuration
3. Save file
4. Run:

```bash
python main.py
```

**Code changes required:** **ZERO**

---

## High-Level Architecture

```text
FreshserviceClient                     JiraClient
   ├─ tickets                           ├─ create_issue
   ├─ conversations                     ├─ update_issue
   ├─ attachments                       ├─ transitions
   └─ agents                            └─ comments