from __future__ import annotations

from typing import Dict, Any, List, Optional
import logging
import re
import html
from datetime import datetime, timezone, timedelta

from clients import FreshserviceClient, JiraClient
from models.fresh_ticket_model import FreshTicket
from models.mapping import (
    Mapping,
    mapping_from_settings,
    resolve_field_ids,
)
from core.fs_to_jira import build_issue_fields as build_fields_core
from utils.config import settings

logger = logging.getLogger(__name__)



class SyncEngine:
    """Main orchestration class for syncing Freshservice tickets to Jira."""
    def __init__(self, fresh: FreshserviceClient, jira: JiraClient, settings, department=None):
        """
        Initialize the sync engine with a Freshservice and Jira client.

        Args:
            fresh: FreshserviceClient instance
            jira: JiraClient instance
            settings: Settings instance
            department: Optional DepartmentConfig for department-specific behavior
        """
        self.fresh = fresh
        self.jira = jira
        self.settings = settings
        self.department = department

        # Build mapping from JSON
        self.mapping: Mapping = mapping_from_settings(settings)
        self._resolved_ids = resolve_field_ids(self.jira, self.mapping.jira)
        logger.info("Resolved Jira field IDs: %s", self._resolved_ids)

        self.sync_comments_enabled: bool = True
        self._user_name_cache: Dict[int, str] = {}



    # --- JQL & Issue Property helpers --- #
    def _field_selector_for_jql(self, logical_key: str) -> str:
        """Return the JQL selector for the given logical custom field."""
        ref_value = getattr(self.mapping.jira, logical_key).ref
        if ref_value.startswith("customfield_"):
            num = ref_value.split("_", 1)[-1]
            return f"cf[{num}]"
        return f'"{ref_value}"'


    def _find_issue(self, fs_ticket_id: int) -> Optional[str]:
        """Find the Jira issue key for the given Freshservice ticket ID."""
        fs_value = str(fs_ticket_id)
        selector = self._field_selector_for_jql("fs_ticket_number")

        # Use department-specific project if available, otherwise fall back to settings
        project_key = self.department.jira_project_key if self.department else self.settings.jira_project_key

        jql = (
            f'project = "{project_key}" '
            f'AND {selector} ~ "{fs_value}" ORDER BY created DESC'
        )
        issues = self._jql(jql, max_results=1)
        if issues:
            return issues[0].get("key")

        jql = (
            f'project = "{project_key}" '
            f'AND summary ~ "[FS-{fs_value}]" ORDER BY created DESC'
        )
        issues = self._jql(jql, max_results=1)
        return issues[0].get("key") if issues else None


    def _jql(self, jql: str, max_results: int = 1) -> List[Dict[str, Any]]:
        """
        Execute a JQL query and return matching issues.

        Supports fallback parsing if 'issues' key is missing
        """
        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status"],
        }
        # Use the standard Jira search endpoint
        resp = self.jira.create_request("/search/jql", "POST", data=payload)
        data = resp.json()
        issues = data.get("issues")
        if issues is None and "issueIds" in data:

            # Fallback for edge cases where Jira returns issueIds instead of full issue object
            issues = [{"id": i} for i in data.get("issueIds", [])]
        return issues or []


    def _get_issue_property(self, issue_key: str, prop_key: str) -> Optional[dict]:
        """
        Get custom properties from Jira issues.

        Returns None if property is not found.
        """
        try:
            resp = self.jira.create_request(f"/issue/{issue_key}/properties/{prop_key}", "GET")
            if resp.status_code == 200:
                return resp.json().get("value")
        except Exception:
            pass
        return None


    def _set_issue_property(self, issue_key: str, prop_key: str, value: dict) -> None:
        """Set JSON-serializable properties on Jira issues."""
        self.jira.create_request(f"/issue/{issue_key}/properties/{prop_key}", "PUT", data=value)


    # --- Workflow alignment --- #
    def _transition_to_status(self, issue_key: str, desired: str) -> None:
        """Transition the given issue to the desired status."""
        try:
            transitions = self.jira.get_transitions(issue_key)
            for t in transitions:
                if t.get("to", {}).get("name") == desired:
                    self.jira.transition_issue(issue_key, t["id"])
                    logger.info("Transitioned %s → %s via %s", issue_key, desired, t.get("name"))
                    return
        except Exception as ex:
            logger.warning("Could not transition %s to %s: %s", issue_key, desired, ex)


    # --- Text utils --- #
    def _strip_html(self, text: str) -> str:
        """Strip HTML tags from the given text. Deliberately local to this class."""
        try:
            s = str(text or "")
        except Exception:
            s = ""
        s = (
            s.replace("<br/>", "\n")
            .replace("<br>", "\n")
            .replace("</p>", "\n")
            .replace("<p>", "")
        )
        s = re.sub(r"<[^>]+>", "", s)
        s = html.unescape(s)
        s = re.sub(r"\n\s*\n\s*", "\n\n", s)
        return s.strip()


    def _format_timestamp(self, value: Any, style: str = "long") -> str:
        """Pretty-format a timestamp."""
        dt: Optional[datetime] = None
        try:
            if isinstance(value, datetime):
                dt = value
            else:
                s = str(value or "")
                if not s:
                    return ""
                if s.endswith("Z"):
                    s = s.replace("Z", "+00:00")
                try:
                    dt = datetime.fromisoformat(s)
                except Exception:
                    try:
                        dt = datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S")
                        dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        return str(value)
        except Exception:
            return str(value)

        try:
            local_dt = dt.astimezone()
            if style == "date":
                return local_dt.strftime("%Y-%m-%d")
            if style == "short":
                return local_dt.strftime("%b %d, %Y %I:%M %p %Z")
            return local_dt.strftime("%b %d, %Y at %I:%M %p %Z")
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z") if dt else str(value)


    # --- Conversation author/name resolution --- #
    def _resolve_author_name(self, conv: dict) -> str:
        """
        Resolve the author name for the given conversation.

        Tries user_name, agent lookup, requestor lookup, and finally email fields.
        Caches agent/requestor names by ID to avoid repeated API calls.
        """
        direct_name = conv.get("user_name") or conv.get("name")
        if isinstance(direct_name, str) and direct_name.strip():
            return direct_name.strip()

        uid = conv.get("user_id")
        try:

            # Convert string user_id to int if possible
            if isinstance(uid, str) and uid.isdigit():
                uid = int(uid)
        except Exception:
            pass

        if isinstance(uid, int):
            cached = self._user_name_cache.get(uid)
            if cached:
                return cached

            name: Optional[str] = None
            try:
                agent = self.fresh.get_agent_by_id(uid)
            except Exception:
                agent = None
            if isinstance(agent, dict) and agent:

                # Use chained fallback logic to extract the most complete name available
                name = (
                    agent.get("name")
                    or (f"{agent.get('first_name', '')} {agent.get('last_name', '')}".strip())
                    or agent.get("email")
                    or (agent.get("contact", {}) or {}).get("email")
                )

            if not name:
                try:
                    req = self.fresh.get_requester_by_id(uid)
                except Exception:
                    req = None
                if isinstance(req, dict) and req:
                    name = (
                        req.get("name")
                        or (f"{req.get('first_name', '')} {req.get('last_name', '')}".strip())
                        or req.get("email")
                        or req.get("primary_email")
                    )

            if name:
                name = str(name).strip()
                self._user_name_cache[uid] = name
                return name

        from_email = conv.get("from_email")
        if isinstance(from_email, str) and from_email.strip():
            return from_email.strip()

        to_emails = conv.get("to_emails")
        if isinstance(to_emails, list):
            return ", ".join([str(x) for x in to_emails if x])
        if isinstance(to_emails, str) and to_emails.strip():
            return to_emails.strip()

        return "Freshservice User"


    # --- Field payload builder --- #
    def _build_fields(self, t: FreshTicket) -> Dict[str, Any]:
        """Build the fields payload for the given Freshservice ticket."""
        # Use department-specific settings if available, otherwise fall back to global settings
        project_key = self.department.jira_project_key if self.department else self.settings.jira_project_key
        issue_type_id = self.department.jira_issue_type_id if self.department else self.settings.jira_issue_type_id

        return build_fields_core(
            mapping=self.mapping,
            resolved_ids=self._resolved_ids,
            jira_client=self.jira,
            t=t,
            project_key=project_key,
            issue_type_id=issue_type_id,
            department=self.department,
        )


    # --- Conversation text --- #
    def _conversation_text(self, conv: dict) -> str:
        """
        Format a FreshService conversation into a readable Jira comment

        Includes author name, timestamp, and body text.
        """

        author = self._resolve_author_name(conv)
        created = self._format_timestamp(conv.get("created_at"), style="date")
        body = conv.get("body_text") or conv.get("body") or conv.get("body_html") or ""
        body = self._strip_html(body)
        return f"{author} on {created} wrote:\n{body}".strip()


    # --- Public API: create or update --- #
    def create_or_update_issue(self, fs_ticket: FreshTicket) -> str:
        """
        Create or update the Jira issue for the given Freshservice ticket.

        - Checks if issue already exists in Jira
        - Applies age filtering
        - Ues Createmeta and Editmeta to validate field updates
        - Syncs comments and transitions issues to 'Backlog' if newly created
        """

        # Try to find an existing Jira issue for this FreshService ticket
        fs_id = fs_ticket.id
        issue_key = self._find_issue(fs_id)

        try:

            # Enrich ticket with requester details (name, phone, location, department)
            enriched = self.fresh.get_ticket(fs_id, include_requester=True)
        except Exception:
            enriched = None

        t_for_fields = enriched or fs_ticket
        fields = self._build_fields(t_for_fields)

        if issue_key:
            # Existing issue: considers age and Editmeta filtering
            try:
                existing = self.jira.get_issue(issue_key)
                created_str = getattr(getattr(existing, "fields", None), "created", None)
                if created_str:
                    dt = None
                    try:
                        dt = datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%S.%f%z")
                    except Exception:
                        try:
                            dt = datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%S%z")
                        except Exception:
                            try:
                                s2 = created_str.replace("Z", "+00:00").replace("+0000", "+00:00")
                                dt = datetime.fromisoformat(s2)
                            except Exception:
                                dt = None
                    if dt is not None:
                        # Skip updates for issues older than cutoff period
                        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cutoff_period)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff:
                            logger.info(
                                "Skipping update for Jira issue %s (created %s) older than %d days.",
                                issue_key,
                                created_str,
                                settings.cutoff_period,
                            )
                            return issue_key
            except Exception as ex:
                logger.warning("Unable to evaluate age for Jira issue %s: %s", issue_key, ex)

            # Immutable on update
            fields.pop("project", None)
            fields.pop("issuetype", None)
            fields.pop("assignee", None)

            # Preserve existing labels and merge with new ones
            if "labels" in fields:
                try:
                    existing_issue = self.jira.get_issue(issue_key)
                    existing_labels = getattr(getattr(existing_issue, "fields", None), "labels", []) or []
                    new_labels = fields.get("labels", [])

                    # Merge labels, avoiding duplicates
                    merged_labels = list(set(existing_labels + new_labels))
                    fields["labels"] = merged_labels
                    logger.debug("Merged labels for %s: existing=%s, new=%s, merged=%s",
                                issue_key, existing_labels, new_labels, merged_labels)
                except Exception as ex:
                    logger.warning("Could not merge labels for %s: %s", issue_key, ex)

            # Filter out fields not editable on the Jira issue screen
            try:
                allowed = self.jira.get_editmeta_fields(issue_key)  # requires helper in clients/jira.py
            except Exception as ex:
                logger.warning("Could not fetch editmeta for %s, proceeding without filter: %s", issue_key, ex)
                allowed = {}

            filtered = {k: v for k, v in fields.items() if (not allowed) or (k in allowed)}
            dropped = [k for k in fields.keys() if k not in filtered.keys()]
            if dropped:
                logger.warning(
                    "Dropping %dfield(s) not on Edit screen for %s: %s",
                    len(dropped), issue_key, sorted(dropped)
                )

            if not filtered:
                logger.info("No editable fields for %s; skipping field update.", issue_key)
            else:
                self.jira.update_issue(issue_key, filtered)
                logger.info("Updated Jira issue %s for FS-%s", issue_key, fs_id)

        else:
            # Create a new Jira issue
            created = self.jira.create_request("/issue", "POST", data={"fields": fields}).json()
            issue_key = created.get("key")
            logger.info("Created Jira issue %s for FS-%s", issue_key, fs_id)

            # Store FreshService metadata on the Jira issue for future syncs
            self._set_issue_property(
                issue_key, "fs_meta", {"fs_ticket_id": str(fs_id), "last_conversation_id": None}
            )

            # Move newly created issue to default status (department-specific or global)
            default_status = self.department.jira_default_status if self.department else settings.jira_status
            self._transition_to_status(issue_key, default_status)

        # Comment sync after issue creation/update
        self._sync_ticket_attachments(t_for_fields, issue_key)
        self._sync_comments(fs_ticket, issue_key)
        return issue_key


    # --- Run (batch) --- #
    def run(self, tickets: List[FreshTicket], issues: List) -> None:
        """
        Process a batch of Freshservice tickets.

        - Filters old tickets using cutoff date
        - Logs skipped tickets
        - Calls create_or_update_issue for each recent ticket
        - Logs processed count
        """
        processed = 0
        # Define a cutoff date for filter old tickets by days
        cutoff = datetime.now(timezone.utc) - timedelta(days=settings.cutoff_period)

        def _coerce_dt(val) -> Optional[datetime]:
            # Normalize datetime values to UTC
            if val is None:
                return None
            if isinstance(val, datetime):
                return val.replace(tzinfo=val.tzinfo or timezone.utc)
            try:
                s = str(val)
                if s.endswith("Z"):
                    s = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return None

        recent_tickets: List[FreshTicket] = []
        skipped = 0

        # Skip tickets with a missing or outdated creation timestamp
        for t in tickets or []:
            dt = _coerce_dt(t.created_at)
            if dt is None or dt < cutoff:

                # Log each skipped ticket for traceability
                skipped += 1
                logger.info(
                    f"Skipping FS-%s: ticket created_at=%s is older than {settings.cutoff_period} days or missing.",
                    t.id,
                    t.created_at,
                )
                continue
            recent_tickets.append(t)

        if skipped:
            logger.info("Age filter: skipped %d Freshservice ticket(s) older than 7 days.", skipped)

        # Attempt to sync each recent ticket to Jira
        for t in recent_tickets:
            try:
                self.create_or_update_issue(t)
                processed += 1
            except Exception as ex:
                logger.exception("Failed to sync FS-%s: %s", getattr(t, "id", "?"), ex)

        logger.info("Sync run completed: processed=%d", processed)


    # --- Attachment helpers --- #
    def _extract_fs_attachments(self, conv: dict) -> list[dict]:
        """Normalize Freshservice attachments to a common format."""
        atts = conv.get("attachments") or []
        norm = []
        for a in atts:
            if not isinstance(a, dict):
                continue
            url = (
                a.get("attachment_url_for_api")
                or a.get("attachment_url")
                or a.get("content_url")
                or a.get("url")
                or ""
            )
            name = a.get("name") or a.get("filename") or "attachment"
            ctype = a.get("content_type") or "application/octet-stream"
            size = a.get("size")
            norm.append({"name": name, "url": url, "content_type": ctype, "size": size})
        return norm

    def _extract_fs_ticket_attachments(self, t: FreshTicket) -> list[dict]:
        """
        Normalize Freshservice *ticket-level* attachments to a common format.
        """
        atts = getattr(t, "custom_fields", None)
        try:
            raw = self.fresh._get_json(f"/api/v2/tickets/{t.id}", params={"include": "requester"}) or {}
        except Exception:
            return []

        # Ticket payload may be under "ticket" or at top-level depending on account/SDK
        ticket_obj = raw.get("ticket", raw if isinstance(raw, dict) else {})
        fs_atts = ticket_obj.get("attachments") or []

        norm: list[dict] = []
        for a in fs_atts:
            if not isinstance(a, dict):
                continue
            url = (
                    a.get("attachment_url_for_api")
                    or a.get("attachment_url")
                    or a.get("content_url")
                    or a.get("url")
                    or ""
            )
            name = a.get("name") or a.get("filename") or "attachment"
            ctype = a.get("content_type") or "application/octet-stream"
            size = a.get("size")
            if url:
                norm.append({"name": name, "url": url, "content_type": ctype, "size": size})
        return norm

    def _build_attachments_block(self, uploaded: list[dict]) -> str:
        """Build the attachments block for the given list of attachments."""

        # Skip if no attachments were uploaded
        if not uploaded:
            return ""
        lines = ["", "Attachments:", "-----------"]
        for j in uploaded:
            name = j.get("filename") or j.get("name") or "file"
            link = j.get("content") or j.get("self") or ""
            size = j.get("size")
            if size is not None:
                lines.append(f"- {name} ({size} bytes): {link}")
            else:
                lines.append(f"- {name}: {link}")
        return "\n".join(lines).strip()


    def _sync_ticket_attachments(self, t: FreshTicket, issue_key: str) -> None:
        """
        Transfer ticket-level attachments from Freshservice to Jira, once each.
        Uses fs_meta.ticket_attachment_ids to avoid duplicates across runs.
        """
        # 1) Read current meta
        meta = self._get_issue_property(issue_key, "fs_meta") or {}
        already = set(str(x) for x in (meta.get("ticket_attachment_ids") or []))

        # 2) Get jira attachment compatibility
        try:
            attach_meta = self.jira.get_attachment_settings()
            jira_enabled = bool(attach_meta.get("enabled", True))
            jira_max = attach_meta.get("uploadLimit")
        except Exception:
            jira_enabled = True
            jira_max = None
        if not jira_enabled:
            return

        # 3) List ticket-level attachments
        fs_atts = self._extract_fs_ticket_attachments(t)
        if not fs_atts:
            return

        # 4) Transfer only new ones
        transferred_ids: list[str] = []
        for a in fs_atts:
            # Prefer explicit 'id' if present; else fall back to a synthetic key
            att_id = str(a.get("id") or f"{a.get('name')}|{a.get('size')}|{a.get('url')}")
            if att_id in already:
                continue

            try:
                size = a.get("size")
                if jira_max and size and int(size) > int(jira_max):
                    logger.warning(
                        "Skipping FS ticket-level attachment '%s' (%s bytes) > Jira max (%s) for %s",
                        a.get("name"), size, jira_max, issue_key
                    )
                    continue

                blob = self.fresh.download_attachment(a["url"])  # uses auth/no-auth fallback
                self.jira.add_attachment(issue_key, a["name"], blob, a.get("content_type"))
                transferred_ids.append(att_id)
                logger.info("Uploaded ticket-level attachment '%s' to %s", a.get("name"), issue_key)

            except Exception as ex:
                logger.warning(
                    "Failed to transfer ticket-level attachment '%s' for FS-%s: %s",
                    a.get("name"), t.id, ex
                )

        # 5) Persist meta if we uploaded any
        if transferred_ids:
            already.update(transferred_ids)
            meta["fs_ticket_id"] = str(t.id)
            meta["ticket_attachment_ids"] = sorted(already)
            self._set_issue_property(issue_key, "fs_meta", meta)

    # --- Comments sync --- #
    def _sync_comments(self, fs_ticket: FreshTicket, issue_key: str) -> None:
        """
        Append new comments to the given Jira issue.

        Uses last synced conversation ID to filter out duplicates.
        Also transfers attachments to Jira.
        """
        if not self.sync_comments_enabled:
            return

        meta = self._get_issue_property(issue_key, "fs_meta") or {}
        last_id = meta.get("last_conversation_id")

        try:

            # Fetch all conversations from FreshService and sort by ID
            convs = self.fresh.get_ticket_conversations(fs_ticket.id) or []
        except Exception as ex:
            logger.warning("Failed to fetch conversations for FS-%s: %s", fs_ticket.id, ex)
            return

        try:
            convs.sort(key=lambda c: int(c.get("id", 0)))
        except Exception:
            pass

        # Filter only new conversations (those with ID > last synced)
        new_convs = [c for c in convs if (last_id is None) or (int(c.get("id", 0)) > int(last_id))]
        if not new_convs:
            return

        try:
            # Upload attachments
            attach_meta = self.jira.get_attachment_settings()
            jira_attachments_enabled = bool(attach_meta.get("enabled", True))
            jira_max_size = attach_meta.get("uploadLimit")
        except Exception:
            jira_attachments_enabled = True
            jira_max_size = None

        for c in new_convs:
            uploaded: list[dict] = []

            # Append each conversation as a Jira comment, including attachments
            if jira_attachments_enabled:
                fs_atts = self._extract_fs_attachments(c)
                for a in fs_atts:
                    try:
                        if jira_max_size and a.get("size") and int(a["size"]) > int(jira_max_size):
                            logger.warning(
                                "Skipping FS attachment '%s' (%s bytes) > Jira max (%s) for %s",
                                a["name"], a.get("size"), jira_max_size, issue_key
                            )
                            continue
                        blob = self.fresh.download_attachment(a["url"])
                        j = self.jira.add_attachment(issue_key, a["name"], blob, a.get("content_type"))
                        uploaded.append(j)
                    except Exception as ex:
                        logger.warning(
                            "Failed to transfer attachment '%s' for FS-%s: %s",
                            a.get("name"), fs_ticket.id, ex
                        )

            body = self._conversation_text(c)
            att_block = self._build_attachments_block(uploaded)
            if att_block:
                body = f"{body}\n\n{att_block}"

            self.jira.add_comment_to_issue(issue_key, body)

            # Update the issue property to track the latest synced conversation
            last_id = int(c["id"])
            meta["fs_ticket_id"] = str(fs_ticket.id)
            meta["last_conversation_id"] = last_id
            self._set_issue_property(issue_key, "fs_meta", meta)

        logger.info(
            "Synced %d conversation(s) (with attachments) to %s; last_id=%s",
            len(new_convs), issue_key, last_id
        )

    # --- Jira → Freshservice status sync --- #
    def sync_jira_status_to_freshservice(self, jira_issues: List[Any]) -> None:
        """
        Synchronize Jira issue status changes back to Freshservice.

        Uses department-specific status_sync_map to determine which Jira statuses
        should update Freshservice ticket status.

        Args:
            jira_issues: List of Jira issue objects to check for status updates

        Implementation:
        - Checks if department has status_sync_map configured
        - For each Jira issue, extracts FS ticket ID from custom field
        - Compares Jira status against status_sync_map
        - Updates Freshservice ticket status if mapping exists
        - Uses issue property tracking to avoid repeated updates (idempotent)
        """
        if not self.department or not self.department.status_sync_map:
            logger.debug("No status sync map configured for department, skipping Jira → FS status sync")
            return

        logger.info("Starting Jira → Freshservice status sync for department '%s'", self.department.name)
        synced_count = 0
        skipped_count = 0

        for issue in jira_issues:
            try:
                issue_key = getattr(issue, "key", None)
                if not issue_key:
                    continue

                # Get current Jira status
                fields = getattr(issue, "fields", None)
                if not fields:
                    continue

                status_obj = getattr(fields, "status", None)
                if not status_obj:
                    continue

                jira_status = getattr(status_obj, "name", None)
                if not jira_status:
                    continue

                # Check if this Jira status should trigger a Freshservice update
                fs_status_code = self.department.get_fs_status_for_jira_status(jira_status)
                if not fs_status_code:
                    continue

                # Extract Freshservice ticket ID from Jira custom field
                fs_ticket_id_field = self._resolved_ids.get("fs_ticket_number")
                if not fs_ticket_id_field:
                    logger.warning("Cannot sync status: fs_ticket_number field not configured")
                    break

                fs_ticket_id_raw = getattr(fields, fs_ticket_id_field, None)
                if not fs_ticket_id_raw:
                    # No FS ticket ID on this Jira issue, skip
                    continue

                # Convert to int
                try:
                    fs_ticket_id = int(fs_ticket_id_raw)
                except (ValueError, TypeError):
                    logger.warning("Invalid FS ticket ID '%s' on Jira issue %s", fs_ticket_id_raw, issue_key)
                    continue

                # Check if we've already synced this status to avoid loops
                meta = self._get_issue_property(issue_key, "fs_meta") or {}
                last_synced_status = meta.get("last_synced_jira_status")

                if last_synced_status == jira_status:
                    # Already synced this status, skip to avoid loops
                    skipped_count += 1
                    continue

                # Update Freshservice ticket status
                logger.info(
                    "Syncing status: Jira %s (%s) → Freshservice FS-%s (status=%s)",
                    issue_key, jira_status, fs_ticket_id, fs_status_code
                )

                try:
                    result = self.fresh.update_ticket(fs_ticket_id, {"status": int(fs_status_code)})
                    if result:
                        # Track that we synced this status
                        meta["last_synced_jira_status"] = jira_status
                        meta["fs_ticket_id"] = str(fs_ticket_id)
                        self._set_issue_property(issue_key, "fs_meta", meta)
                        synced_count += 1
                        logger.info("Successfully updated FS-%s status to %s", fs_ticket_id, fs_status_code)
                    else:
                        logger.warning("Failed to update FS-%s status (no result returned)", fs_ticket_id)
                except Exception as ex:
                    logger.exception("Failed to update FS-%s status: %s", fs_ticket_id, ex)

            except Exception as ex:
                logger.exception("Error processing Jira issue for status sync: %s", ex)

        logger.info(
            "Jira → Freshservice status sync completed: synced=%d, skipped=%d",
            synced_count, skipped_count
        )
