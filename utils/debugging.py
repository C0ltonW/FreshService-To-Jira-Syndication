import logging
import json
from typing import List, Iterable, Any, Dict, Optional
from clients import JiraClient, FreshserviceClient
from core.sync_engine import SyncEngine
from utils.config import settings

"""
Verbose debugging utilities.
Prints:
    FS custom_fields/requester snapshot
    Department resolution preview
    Resolved Jira field IDs used by SyncEngine
    EditMeta allow-list for the resolved Jira issue (if any)
    Small subset of fields to preview for quick visual inspection
"""


def print_all_jira_accounts(jira_client: JiraClient):
    """Fetch all Jira users and print their emails and accountIds."""
    try:
        users = getattr(jira_client, "list_all_users", None)
        if callable(users):
            users = jira_client.list_all_users()
        else:
            # Fallback to a single page if method unavailable
            resp = jira_client.create_request("/users/search", "GET")
            users = resp.json() or []
        for user in users or []:
            email = user.get("emailAddress") or user.get("email") or user.get("name")
            account_id = user.get("accountId")
            if account_id:
                label = email or user.get("displayName") or "<no-email>"
                print(f"{label}: {account_id}")
    except Exception as e:
        print(f"Error fetching Jira users: {e}")


def print_department_id_name_map(client: FreshserviceClient):
    """
    Print a mapping of department_id to department name for debugging.
    Args:
        client (FreshserviceClient): An instance of the Freshservice client.
    """
    departments = client.get_departments()
    mapping = {dept["id"]: dept["name"] for dept in departments if "id" in dept and "name" in dept}
    print("\n--- FreshService Department ID to Name Mapping ---")
    for dept_id, name in mapping.items():
        print(f"{dept_id}: {name}")


def print_group_id_name_map(client: FreshserviceClient):
    """
    Print a mapping of group_id to group name for debugging.
    Args:
        client (FreshserviceClient): An instance of the Freshservice client.
    """
    groups = client.get_groups()
    mapping = {g["id"]: g["name"] for g in groups if "id" in g and "name" in g}
    print("\n--- FreshService Group ID to Name Mapping ---")
    for group_id, name in mapping.items():
        print(f"{group_id}: {name}")




def print_category_and_subcategory_id_name_map(
    client: FreshserviceClient,
    *,
    contains: str | None = None
) -> None:
    """
    Print FreshService Categories and their Subcategories as a flat,
    human-friendly list using the same banner/format as the Department mapping:

    1234567890: <Category Name>
      - 2345678901: <Subcategory Name>
      - 2345678902: <Subcategory Name>

    Optional:
      contains="printer" → filters to categories/subcategories that match (case-insensitive).
    """
    try:
        fields = client.get_ticket_fields()
    except Exception as e:
        print(f"Error fetching ticket fields: {e}")
        return

    def _label_of(option: dict) -> str:
        # be defensive with Freshservice schema variations
        return (option.get("label") or option.get("value") or option.get("name") or "").strip()

    def _id_of(option: dict):
        # ids can appear under different keys across accounts/export versions
        return option.get("id") or option.get("value") or option.get("name")

    # Find the dependent 'Category' field
    category_field = None
    for f in fields or []:
        name = (f.get("name") or "").lower()
        label = (f.get("label") or f.get("label_for_customers") or "").strip().lower()
        if name == "category" or label == "category":
            category_field = f
            break

    print("\n--- FreshService Categories and Subcategories (ID → Name) ---")
    if not category_field:
        print("Category field not found in Freshservice ticket fields.")
        return
    # Build a normalized set of rows so we can filter/sort and then print
    rows: list[tuple[str, str, str | None, str | None]] = []
    # rows: (cat_id, cat_name, sub_id, sub_name)  # sub_* can be None

    for top in (category_field.get("choices") or []):
        cat_name = _label_of(top)
        cat_id = _id_of(top)
        if not cat_name:
            continue

        subs = top.get("nested_options") or []
        if not subs:
            rows.append((str(cat_id), cat_name, None, None))
            continue

        for sub in subs:
            sub_name = _label_of(sub)
            sub_id = _id_of(sub)
            if not sub_name:
                continue
            rows.append((str(cat_id), cat_name, str(sub_id), sub_name))

    # Optional filtering
    if contains:
        q = contains.lower()
        rows = [
            r for r in rows
            if (q in (r[1] or "").lower()) or (q in (r[3] or "").lower())
        ]

    # Sort by Category Name, then Subcategory Name
    rows.sort(key=lambda r: ((r[1] or "").lower(), (r[3] or "").lower() if r[3] else ""))

    if not rows:
        print("(no categories/subcategories matched your filter)" if contains else "(no categories/subcategories found)")
        return
    # Pretty-print in the requested format
    current_cat_id = None
    for cat_id, cat_name, sub_id, sub_name in rows:
        if cat_id != current_cat_id:
            print(f"{cat_id}: {cat_name}")
            current_cat_id = cat_id
        if sub_id and sub_name:
            print(f"  - {sub_id}: {sub_name}")



class DryRunSyncEngine:
    """
    Wrapper around SyncEngine that intercepts all write operations and prints them to terminal.

    Used for testing and validation without making actual API changes.
    Prints JSON payloads that would be sent to Jira/Freshservice.
    """

    def __init__(self, sync_engine: SyncEngine):
        self.engine = sync_engine
        self.logger = logging.getLogger(__name__)

    def _print_json(self, title: str, data: Any) -> None:
        """Pretty-print JSON data with a title banner."""
        print("\n" + "="*80)
        print(f"DRY-RUN: {title}")
        print("="*80)
        try:
            print(json.dumps(data, indent=2, default=str))
        except Exception:
            print(str(data))
        print("="*80 + "\n")

    def create_or_update_issue(self, fs_ticket):
        """Dry-run version of create_or_update_issue - shows what would be sent."""
        fs_id = fs_ticket.id
        issue_key = self.engine._find_issue(fs_id)

        # Enrich ticket
        try:
            enriched = self.engine.fresh.get_ticket(fs_id, include_requester=True)
        except Exception:
            enriched = None

        t_for_fields = enriched or fs_ticket
        fields = self.engine._build_fields(t_for_fields)

        if issue_key:
            print(f"\n🔍 Found existing Jira issue: {issue_key}")

            # Immutable on update
            fields.pop("project", None)
            fields.pop("issuetype", None)
            fields.pop("assignee", None)

            # Preserve labels
            if "labels" in fields:
                try:
                    existing_issue = self.engine.jira.get_issue(issue_key)
                    existing_labels = getattr(getattr(existing_issue, "fields", None), "labels", []) or []
                    new_labels = fields.get("labels", [])
                    merged_labels = list(set(existing_labels + new_labels))
                    fields["labels"] = merged_labels
                    print(f"📋 Labels: existing={existing_labels}, new={new_labels}, merged={merged_labels}")
                except Exception as ex:
                    self.logger.warning("Could not merge labels for %s: %s", issue_key, ex)

            # Filter via editmeta
            try:
                allowed = self.engine.jira.get_editmeta_fields(issue_key)
            except Exception:
                allowed = {}

            filtered = {k: v for k, v in fields.items() if (not allowed) or (k in allowed)}
            dropped = [k for k in fields.keys() if k not in filtered.keys()]

            if dropped:
                print(f"⚠️  Dropping {len(dropped)} field(s) not on Edit screen: {sorted(dropped)}")

            self._print_json(f"UPDATE Jira Issue {issue_key} (FS-{fs_id})", {
                "issue_key": issue_key,
                "fs_ticket_id": fs_id,
                "operation": "UPDATE",
                "fields": filtered,
                "dropped_fields": dropped
            })
        else:
            print(f"\n✨ No existing issue found - would CREATE new issue")
            self._print_json(f"CREATE Jira Issue (FS-{fs_id})", {
                "fs_ticket_id": fs_id,
                "operation": "CREATE",
                "fields": fields
            })

            # Show what would happen after creation
            print(f"📌 After creation, would set issue property 'fs_meta' with tracking data")
            print(f"📌 Would transition to status: {self.engine.department.jira_default_status if self.engine.department else 'Backlog'}")

        # Show comment/attachment sync plans
        print(f"\n💬 Would sync comments and attachments for FS-{fs_id}")
        return issue_key or f"DRY-RUN-KEY-{fs_id}"

    def run(self, tickets, issues):
        """Dry-run version of run - processes tickets without writing."""
        print("\n" + "🔥"*40)
        print("DRY-RUN MODE ENABLED - No actual changes will be made")
        print("🔥"*80 + "\n")

        for ticket in tickets:
            try:
                self.create_or_update_issue(ticket)
            except Exception as ex:
                self.logger.exception("Dry-run failed for FS-%s: %s", getattr(ticket, "id", "?"), ex)

        print("\n" + "✅"*40)
        print("DRY-RUN COMPLETE - Review output above")
        print("✅"*80 + "\n")

    def sync_jira_status_to_freshservice(self, jira_issues):
        """Dry-run version of status sync."""
        if not self.engine.department or not self.engine.department.status_sync_map:
            print("\n⚠️  No status_sync_map configured - would skip Jira → FS status sync")
            return

        print("\n🔄 DRY-RUN: Jira → Freshservice Status Sync")
        print(f"📋 Status map: {self.engine.department.status_sync_map}")

        updates = []
        for issue in jira_issues:
            try:
                issue_key = getattr(issue, "key", None)
                if not issue_key:
                    continue

                fields = getattr(issue, "fields", None)
                if not fields:
                    continue

                status_obj = getattr(fields, "status", None)
                jira_status = getattr(status_obj, "name", None) if status_obj else None

                if not jira_status:
                    continue

                fs_status_code = self.engine.department.get_fs_status_for_jira_status(jira_status)
                if not fs_status_code:
                    continue

                fs_ticket_id_field = self.engine._resolved_ids.get("fs_ticket_number")
                if not fs_ticket_id_field:
                    continue

                fs_ticket_id_raw = getattr(fields, fs_ticket_id_field, None)
                if not fs_ticket_id_raw:
                    continue

                try:
                    fs_ticket_id = int(fs_ticket_id_raw)
                except (ValueError, TypeError):
                    continue

                updates.append({
                    "jira_issue": issue_key,
                    "jira_status": jira_status,
                    "fs_ticket_id": fs_ticket_id,
                    "fs_status_code": fs_status_code
                })
            except Exception:
                pass

        if updates:
            self._print_json("Jira → Freshservice Status Updates", updates)
        else:
            print("⚠️  No status updates needed\n")


def _debug_inspect_ticket_sync(fresh_client: FreshserviceClient,
                               jira_client: JiraClient,
                               sync_engine: SyncEngine,
                               fs_ticket,
                               dry_run: bool = False):
    """
    Debug a single FreshService ticket's sync process.

    - Enriches ticket with requester info
    - Logs custom fields and requester types
    - Shows department resolution logic
    - Build Jira field payload and compares to EditMeta allow list

    Args:
        dry_run: If True, shows what would be sent without making API calls
    """
    logger = logging.getLogger(__name__)

    try:
        # Enrich ticket with requester details for realistic sync preview
        enriched = fresh_client.get_ticket(fs_ticket.id, include_requester=True)
    except Exception:
        enriched = None
    t = enriched or fs_ticket

    # Quick snapshot of what FS gives
    try:
        cf = getattr(t, "custom_fields", {}) or {}
        logger.info("FS-%s custom_fields keys: %s", t.id, sorted(cf.keys()))
        if getattr(t, "requester", None):
            present = [k for k in ["name", "phone", "location"] if getattr(t.requester, k, None) is not None]
            logger.info("FS-%s requester keys present: %s", t.id, present)
    except Exception:
        pass

    try:
        dept_on_ticket = getattr(t, "department_id", None)
        requester_id = getattr(t, "requester_id", None)

        # If ticket lacks department, try to infer it from requester profile
        if dept_on_ticket is None and requester_id:
            full_req = None
            try:
                full_req = fresh_client.get_requester_by_id(int(requester_id))
            except Exception as ex:
                logger.warning("FS-%s requester deep-dive failed: %s", t.id, ex)

            if isinstance(full_req, dict) and full_req:
                fr_dept_id = full_req.get("department_id")
                fr_dept_ids = full_req.get("department_ids") or []
                logger.info(
                    "FS-%s requester deep-dive | requester_id=%s department_id=%s department_ids=%s",
                    t.id, requester_id, fr_dept_id, fr_dept_ids
                )
            else:
                logger.info(
                    "FS-%s requester deep-dive | requester_id=%s no full requester data returned",
                    t.id, requester_id
                )
        else:
            logger.info(
                "FS-%s requester deep-dive skipped | department_id on ticket=%s requester_id=%s",
                t.id, dept_on_ticket, requester_id
            )
    except Exception as ex:
        logger.warning("Requester deep-dive logging failed for FS-%s: %s", t.id, ex)

    # Print raw inputs for spotting missing values
    requester_present = bool(getattr(t, "requester", None))
    requester_phone = getattr(getattr(t, "requester", None), "phone", None)
    requester_loc = getattr(getattr(t, "requester", None), "location", None)
    logger.info(
        "FS-%s raw inputs | dept_id=%s cat=%s subcat=%s "
        "requester?=%s requester.phone=%s location=%s requester.location=%s "
        "urgency=%s impact=%s source=%s group_id=%s",
        t.id,
        getattr(t, "department_id", None),
        getattr(t, "category", None),
        getattr(t, "sub_category", None),
        requester_present,
        requester_phone,
        getattr(t, "location", None),
        requester_loc,
        getattr(t, "urgency", None),
        getattr(t, "impact", None),
        getattr(t, "source", None),
        getattr(t, "group_id", None),
    )

    # Build the exact Jira field payload that would be sent
    fields = sync_engine._build_fields(t)

    # 3a) Department preview (what the builder decided)
    try:
        resolved_ids = sync_engine._resolved_ids or {}
        dept_field_id = resolved_ids.get("department")
        if dept_field_id:
            dept_preview = fields.get(dept_field_id, None)
            logger.info("FS-%s Department preview | jira_field=%s value=%s",
                        t.id, dept_field_id, dept_preview)
        else:
            logger.info("FS-%s Department preview | jira_field not resolved", t.id)
    except Exception:
        pass

    # 4) Print the resolved IDs and what we would send
    resolved_ids = sync_engine._resolved_ids
    logger.info("Resolved Jira field IDs used by SyncEngine: %s", resolved_ids)

    # Separate customfields from system ones for readability
    would_send = list(fields.keys())
    logger.info("Would send (%d): %s", len(would_send), would_send)

    # Compare allowed fields vs what we would send; log any dropped fields
    issue_key = sync_engine._find_issue(t.id)
    if issue_key:
        try:
            allowed = jira_client.get_editmeta_fields(issue_key)
        except Exception as ex:
            logger.warning("EditMeta fetch failed for %s: %s", issue_key, ex)
            allowed = {}

        allowed_keys = sorted(list(allowed.keys())) if allowed else []
        dropped = sorted([k for k in would_send if allowed and k not in allowed])
        if allowed:
            logger.info("- Allowed by Edit screen (%d): %s", len(allowed_keys), allowed_keys)
        if dropped:
            logger.info("- DROPPED (not on Edit screen) (%d): %s", len(dropped), dropped)
        else:
            logger.info("- DROPPED: none (all are on the Edit screen)")
    else:
        logger.info("No existing Jira issue found for FS-%s; this would be a CREATE payload.", t.id)

    # 6) (Optional) pretty-print a subset of field -> value for quick visual
    preview_keys = [k for k in would_send if k.startswith("customfield_")] + ["summary", "priority", "description"]
    preview = {k: fields.get(k) for k in preview_keys if k in fields}
    logger.info("Preview payload (subset): %s", preview)



def run_test_mode(
    fresh_client: FreshserviceClient,
    jira_client: JiraClient,
    sync_engine: SyncEngine,
    aggregated_tickets: List,
    aggregated_issues: List,
) -> None:
    """
    Test mode runner for debugging.

    Behavior controlled by settings:
    - IS_TEST=true: Enables test mode
    - SYNC_TEST_TICKET=true: Actually syncs to Jira/Freshservice
    - SYNC_TEST_TICKET=false: Dry-run mode (shows what would be sent without making changes)
    - TEST_TICKET_ID: Specific ticket ID to test (optional)

    - Prints valid Jira issue types.
    - Dumps Jira fields (name -> id).
    - Runs sync on test ticket (real or dry-run depending on SYNC_TEST_TICKET).
    - Compares payload vs EditMeta.
    """
    logger = logging.getLogger(__name__)

    # Determine run mode
    is_dry_run = not settings.sync_test_ticket
    mode_str = "DRY-RUN" if is_dry_run else "LIVE SYNC"

    print("\n" + "="*80)
    print(f"TEST MODE: {mode_str}")
    print("="*80)
    if is_dry_run:
        print("⚠️  SYNC_TEST_TICKET=false - No actual API changes will be made")
        print("    Set SYNC_TEST_TICKET=true to enable actual syncing")
    else:
        print("✅ SYNC_TEST_TICKET=true - Will make actual API changes")
    print("="*80 + "\n")

    logger.info("Running in TEST MODE (%s)", mode_str)


    # --- Department and Group Mapping Preview --- #
    print_group_id_name_map(fresh_client)
    print_department_id_name_map(fresh_client)

    # --- Category/Subcategory IDs preview --- #
    print_category_and_subcategory_id_name_map(fresh_client)

    # --- 0) Show valid issue types for quick sanity --- #
    try:
        issue_types = jira_client.get_issue_types_for_project(settings.jira_project_key)
        print("\nValid Issue Types for Project:")
        for it in issue_types:
            print(f"- {it['name']} (ID: {it['id']})")
    except Exception as e:
        logger.exception(f"Failed to fetch issue types for project {settings.jira_project_key}: {e}")

    # --- 1) Dump Jira fields (name -> id) once so you can copy customfield_* ids --- #
    try:
        print("\nJira Fields (name -> id):")
        resp = jira_client.create_request("/field", "GET")
        for f in resp.json():
            name = f.get("name")
            fid = f.get("id")
            if name and fid:
                print(f"{name:<40} -> {fid}")
    except Exception as e:
        logger.exception(f"Failed to list Jira fields: {e}")

    # --- 2) Show resolved ids that SyncEngine will use --- #
    try:
        print("\nResolved Jira field IDs used by SyncEngine:")
        print(sync_engine._resolved_ids)
    except Exception:
        pass


    # Determine which ticket to test
    test_id = settings.test_ticket_id or settings.fresh_test_ticket
    print(f"\n🎯 Testing with ticket ID: {test_id}")

    test_tickets = [t for t in aggregated_tickets if getattr(t, "id", None) == test_id]
    if not test_tickets:
        try:
            one = fresh_client.get_ticket(test_id, include_requester=True)
            if one:
                test_tickets = [one]
        except Exception as e:
            logger.exception(f"Failed to fetch test ticket id={test_id}: {e}")

    if not test_tickets:
        logger.warning(f"No test ticket found for id={test_id}. Test run will be a no-op.")
        return

    for t in test_tickets:
        print(
            f"\n📋 Test Ticket: id={t.id} subject={t.subject} "
            f"requester_id={t.requester_id} status={t.status}"
        )
        _debug_inspect_ticket_sync(fresh_client, jira_client, sync_engine, t, dry_run=is_dry_run)
        print_all_jira_accounts(jira_client)

    # --- 3) Sync pass (real or dry-run) --- #
    if is_dry_run:
        print("\n" + "🔥"*40)
        print("Starting DRY-RUN sync pass...")
        print("🔥"*80)
        dry_run_engine = DryRunSyncEngine(sync_engine)
        dry_run_engine.run(test_tickets, aggregated_issues)
        dry_run_engine.sync_jira_status_to_freshservice(aggregated_issues)
    else:
        print("\n" + "⚡"*40)
        print("Starting LIVE sync pass...")
        print("⚡"*80)
        sync_engine.run(test_tickets, aggregated_issues)

    # --- 4) Post-sync inspection (only for live sync) --- #
    if not is_dry_run:
        try:
            issue_key = sync_engine._find_issue(test_id)
            if issue_key:
                print(f"\n✅ Post-sync: Found Jira issue {issue_key}")
                print(f"    EditMeta validation check...")
                editmeta_fields = jira_client.get_editmeta_fields(issue_key)
                editable_keys = set(editmeta_fields.keys())

                enriched = fresh_client.get_ticket(test_id, include_requester=True) or test_tickets[0]
                fields_payload = sync_engine._build_fields(enriched)

                fields_payload.pop("project", None)
                fields_payload.pop("issuetype", None)

                would_send = set(fields_payload.keys())
                allowed = would_send & editable_keys
                dropped = would_send - editable_keys

                print(f"    - Would send ({len(would_send)}): {sorted(would_send)}")
                print(f"    - Allowed by Edit screen ({len(allowed)}): {sorted(allowed)}")
                if dropped:
                    print(f"    - DROPPED ({len(dropped)}): {sorted(dropped)}")
                else:
                    print("    - DROPPED: none")

            else:
                print("\n⚠️  EditMeta check skipped: issue not found yet")
        except Exception as e:
            logger.exception(f"Failed to compare payload vs EditMeta: {e}")

        # --- 5) Second pass to exercise update path --- #
        print("\n⚡ Running second sync pass to test idempotency...")
        try:
            refreshed_issues = jira_client.get_board_issues(settings.jira_board_id)
            sync_engine.run(test_tickets, refreshed_issues)
            print("✅ Second pass complete")
        except Exception as e:
            logger.exception(f"Failed to perform second pass in test mode: {e}")

    print("\n" + "="*80)
    print(f"TEST MODE COMPLETE ({mode_str})")
    print("="*80)
    logger.info("TEST MODE sync completed (%s)", mode_str)