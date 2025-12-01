# FreshService-To-Jira-Syndication
**Purpose**: Keep Jira issues in sync with FreshService(FS) tickets. 

Treats FS tickets as source of truth, and updates Jira issues accordingly.

---

What this tool does
1) Creates Jira issues from Freshservice tickets

   * Builds a Jira issue whose summary includes a stable token ([FS-<id>]) and whose description is a clean, HTML‑stripped version of the FS ticket description.
   * Populates a configurable set of Jira custom fields with details derived from FS (e.g., status/urgency/impact/department/category/subcategory/location/requestor metadata).
   * Uses Jira createmeta to assign fields correctly: it chooses from allowed options when a field is pick‑list‑backed, or falls back to plain text when freeform is expected.
   * Ensures Backlog (or your equivalent “intake” column) is the landing state for new issues to keep triage predictable.


2) Updates existing issues idempotently

   * Finds the corresponding Jira issue by: 
     * The configured custom field holding the FS Ticket #, or
     * A summary token like [FS-12345].
   * If found, updates only the fields that are editable on the issue’s current Edit screen (using Jira’s EditMeta), removing the guesswork and avoiding 400‑level rejections for fields that can’t be set in the current screen scheme.
   * Skips updates for Jira issues older than N days (e.g., 7) to avoid churning historical tickets when you re‑run or re‑map.


3) Mirrors Freshservice conversations as Jira comments

   * Appends new FS conversations (notes/replies) to the Jira issue as plain‑text comments.
   * Adds any attachments that are within Jira’s configured upload limit.
   * Maintains a per‑issue cursor (fs_meta.last_conversation_id) so reruns only sync incremental conversations—no duplicates.


4) Aligns Jira workflow with Freshservice status

   * Maps FS numeric status codes to Jira status names (e.g., FS “In Progress” → Jira “In Progress”), then transitions the issue only when a valid transition exists.
   * Avoids breaking workflows by never forcing state: it inspects available transitions and uses a safe, “best‑effort” approach.


# High‑level architecture

```
FreshserviceClient                     JiraClient
   ├─ get_ticket / get_all_agents         ├─ create_issue / update_issue
   ├─ filter_tickets_by_agent             ├─ search (JQL) / get_editmeta_fields
   ├─ get_ticket_conversations            ├─ add_comment_to_issue / add_attachment
   └─ download_attachment                 └─ transitions / createmeta / properties
            └──────────────┐            ┌──────────────┘
                           ▼            ▼
                         SyncEngine (core logic)
                           ├─ find-or-create issue (idempotent)
                           ├─ build fields (fs_to_jira mapping)
                           ├─ apply EditMeta filter, update/create
                           ├─ set fs_meta property (cursor)
                           ├─ transition toward desired status
                           └─ sync new conversations (+ attachments)

fs_to_jira.py
   └─ pure transformation of Freshservice ticket → Jira "fields" payload
      (HTML strip, enum/name mapping, createmeta-aware option selection)
```

### Design Goals:
* Clients are thin and focused on HTTP details and retries.
* The SyncEngine owns orchestration (find/create/update, comments, transitions).
* The mapping module (fs_to_jira.py) is pure and testable (input → output).
---
## Data mapping (what gets copied)

* Summary: "[FS-<id>] <subject>" for recognizable traceability.
* Description: Freshservice description, HTML stripped and unescaped to readable text, wrapped into Jira’s minimal ADF document.
* Custom fields (configurable by display name or customfield_####):
  * FS Ticket Number
  * FS Status (numeric → friendly name via your mapping)
  * Source (numeric → friendly name, e.g., Email/Portal/Phone/Chat)
  * Ticket Type
  * Urgency, Impact (numeric → friendly name)
  * Group (passed as text)
  * Department (numeric → friendly name; falls back to id if unknown)
  * Ticket Category, Ticket Subcategory
  * Business Requestor (from requester name)
  * Requestor’s Phone
  * Location (Ticket)
  * User‑assigned Category/Subcategory (extracted from the description if present)
  
#### Jira Createmeta awareness:

For each destination field, the mapper looks at the field schema. If the field has allowedValues, it picks the matching option ({"value": "<exact-match>"}) when possible; otherwise it passes a simple string. This prevents invalid option assignments.

---

## Idempotency & safety

* **Issue lookup** is repeatable (custom field or [FS-<id>] in the summary).
* **EditMeta filtering** prevents updates to fields not present on the issue’s current Edit screen, reducing noisy 400s and audit friction.
* **Age cut‑off** (e.g., 7 days) prevents stale issues from being churned on full reruns after mapping tweaks.
* **Conversation cursor** on the Jira issue (fs_meta) ensures you only mirror new FS comments/attachments.

---

## Comment Mirroring (As appears in Jira)
Each comment is rendered as:
```
<Author or Email> on <YYYY-MM-DD> wrote:
<plain text body without HTML>

Attachments:
- <file-1> (<size> bytes): <link>
- <file-2>: <link>
```

* Author resolution tries (in order): conversation name → agent lookup → requester lookup → email fields → fallback "Freshservice User".
* Attachments are copied if Jira has attachments enabled and the file is under Jira’s configured size limit; otherwise, the comment is posted without the file, and a warning is logged.

---

## Status alignment (Freshservice → Jira)

* FS status codes are mapped to target Jira status names (configurable).
* The engine fetches available transitions for the issue and applies the one that leads to the desired status (if present).
* If no valid transition exists, the engine logs a warning but doesn’t force a state change.

---

## Limitations

* One-way sync (FS → Jira)
* Schema dependancy: If Jira screen scheme hids a field (not on Edit), sync engine will not update it by design. I will log it was dropped
* Name Matching: Option assignment requires exact text match. If Jira option differs in punctuation/case, update the mapping tables.
  * These changes are made in mapping.py in def mapping_from_env() in mapping.py.
* Attachment size: If Jira has attachments disabled, attachments will be dropped.

---

## Glossary

* **Createmeta**: Jira endpoint that exposes field schema and options
* **EditMeta**: Jira endpoint that exposes field schema and options for the current screen
* **JQL**: Jira Query Language. For filtering issues.
* **ADF**: Atlassian Document Format. Used for minimal Jira comment formatting.


---

## Setup & Configuration

- Copy settings/settings.env.txt to settings/settings.env and fill in your own values:
  - FRESHSERVICE_DOMAIN, FRESHSERVICE_API_KEY
  - JIRA_DOMAIN, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY, JIRA_ISSUE_TYPE_ID, JIRA_BOARD_ID
  - Behavior flags like IS_TEST, SYNC_ALWAYS_CREATE, CUTOFF_PERIOD, AGENTS_TO_SYNC
- Review settings/mappings.json and customize placeholder values to match your environment (department IDs/names, category-to-agent mappings, status/priority names, Jira customfield IDs, etc.). The file in this repo contains only generic example values.
- The repo includes a .gitignore that excludes settings/settings.env and other local files. Do not commit real credentials.

## Project structure

```
FreshService-To-Jira-Syndication/
├─ README.md                          # Project overview and usage notes
├─ requirements.txt                   # Python dependencies
├─ main.py                            # Entry point: wiring, previews, running test/production sync
├─ clients/
│  ├─ __init__.py                     # Exposes JiraClient and FreshserviceClient
│  ├─ freshservice.py                 # FreshserviceClient: HTTP session, pagination, agents/tickets/conversations, downloads
│  └─ jira.py                         # JiraClient: create/update/search issues, comments, attachments, transitions, field resolution
├─ core/
│  ├─ fs_to_jira.py                   # Pure mapping: FS ticket → Jira fields (HTML strip, option picking, createmeta-aware)
│  └─ sync_engine.py                  # Orchestration: find/create/update issues, status transitions, comment and attachment sync
├─ models/
|  ├─ jira_fields.py                  # Pydantic models for Jira field references
│  ├─ fresh_ticket_model.py           # Pydantic models for Freshservice tickets, conversations, requester
│  ├─ jira_issue_model.py             # Pydantic models for Jira issue and fields (typed envelope for responses)
│  └─ mapping.py                      # Mapping schema, mapping_from_settings(), resolve_field_ids(), desired_jira_status()
├─ settings/
│  ├─ settings.env                    # Environment variables loaded by utils.config.Settings
│  ├─ settings.env.txt                # Example/template environment file
│  └─ mappings.json                   # All name/option/status/priority maps and Jira field refs (source of truth)
└─ utils/
   ├─ config.py                       # Settings loader (pydantic-settings); reads mappings.json into strongly-typed fields
   ├─ debugging.py                    # Test-mode helpers: previews, debug ticket/issue inspection
   ├─ exceptions.py                   # Custom exception hierarchy used by clients and engine
   └─ logger.py                       # Basic logging configuration
```

What each part handles
- main.py: Boots logging and settings, constructs clients and SyncEngine, previews agents/issues, and runs either test mode or production sync.
- clients/freshservice.py: Handles all Freshservice API calls including pagination, agents/tickets lookup, conversations, and secure attachment downloads with retry.
- clients/jira.py: Handles all Jira Cloud REST calls: create/update issues, JQL search, transitions, comments, attachments, user lookups, and ADF conversion.
- core/fs_to_jira.py: Stateless transformation layer that builds the Jira fields payload from a Freshservice ticket and mapping rules. Responsible for HTML stripping, friendly-name resolution, option selection against createmeta, and safe text fallbacks.
- core/sync_engine.py: The coordinator. Locates existing Jira issues, applies EditMeta filtering, creates or updates issues, mirrors new conversations and attachments, sets per-issue cursors, and aligns Jira status to mapped FS status.
- models/*: Pydantic models for typed, validated data structures (Freshservice ticket shapes, Jira issue envelopes, and the Mapping schema).
- utils/config.py: Centralized settings loader. Reads .env for credentials/behavior flags and mappings.json for all mapping tables and Jira field references.
- utils/debugging.py: A collection of console utilities for test mode to inspect mappings, accounts, tickets, and dry-run behavior.
- utils/exceptions.py: Project-specific exception types surfaced by the clients and engine.
- utils/logger.py: Minimal logging setup used by main and other modules.
- settings/mappings.json: The single source of truth for friendly labels, FS→name maps, Jira field references (names or customfield IDs), priority map, and status targets.
- settings/settings.env(.txt): Environment configuration (domain, credentials, board/project/issue type, agent filters, etc.).
