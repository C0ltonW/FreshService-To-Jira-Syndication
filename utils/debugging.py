import logging
from typing import List, Iterable, Any
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



def _debug_inspect_ticket_sync(fresh_client: FreshserviceClient,
                               jira_client: JiraClient,
                               sync_engine: SyncEngine,
                               fs_ticket):
    """
    Debug a single FreshService ticket's sync process.

    - Enriches ticket with requester info
    - Logs custom fields and requester types
    - Shows department resolution logic
    - Build Jira field payload and compares to EditMeta allow list
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

    - Prints valid Jira issue types.
    - Dumps Jira fields (name -> id).
    - Runs a single sync pass on a test ticket.
    - Compares payload vs EditMeta.
    - Runs second sync pass to exercise update path.
    """
    logger = logging.getLogger(__name__)
    logger.info("Running in TEST MODE")


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


    test_id = settings.fresh_test_ticket
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
            f"Test mode: ticket id={t.id} subject={t.subject} "
            f"requester_id={t.requester_id} status={t.status}"
        )
        _debug_inspect_ticket_sync(fresh_client, jira_client, sync_engine, t)
        print_all_jira_accounts(jira_client)

    # --- 3) First sync pass (create or update) --- #
    sync_engine.run(test_tickets, aggregated_issues)

    # --- 4) Inspect EditMeta vs the fields we would send (post-first-pass) --- #
    try:
        # Try to find the Jira issue for this FS ticket
        issue_key = sync_engine._find_issue(test_id)
        if issue_key:
            print(f"\nEditMeta check for {issue_key}:")
            editmeta_fields = jira_client.get_editmeta_fields(issue_key)
            editable_keys = set(editmeta_fields.keys())

            # Rebuild the field payload exactly like the engine would
            enriched = fresh_client.get_ticket(test_id, include_requester=True) or test_tickets[0]
            fields_payload = sync_engine._build_fields(enriched)

            # Drop immutable on update (same as engine)
            fields_payload.pop("project", None)
            fields_payload.pop("issuetype", None)

            would_send = set(fields_payload.keys())
            allowed = would_send & editable_keys
            dropped = would_send - editable_keys

            print(f"- Would send ({len(would_send)}): {sorted(would_send)}")
            print(f"- Allowed by Edit screen ({len(allowed)}): {sorted(allowed)}")
            if dropped:
                print(f"- DROPPED (not on Edit screen) ({len(dropped)}): {sorted(dropped)}")
            else:
                print("- DROPPED: none (all are on the Edit screen)")

        else:
            print("\nEditMeta check skipped: issue not found yet (may be on first create pass).")
    except Exception as e:
        logger.exception(f"Failed to compare payload vs EditMeta: {e}")

    # --- 5) Second pass like before (exercise update path/idempotency) --- #
    try:
        refreshed_issues = jira_client.get_board_issues(settings.jira_board_id)
        sync_engine.run(test_tickets, refreshed_issues)
    except Exception as e:
        logger.exception(f"Failed to perform second pass in test mode: {e}")

    logger.info("TEST MODE sync completed.")