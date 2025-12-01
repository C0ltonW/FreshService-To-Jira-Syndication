from __future__ import annotations

from typing import Dict, Any, Optional
import logging
import re
import html

from models.mapping import Mapping
from models.fresh_ticket_model import FreshTicket
from utils.config import settings

logger = logging.getLogger(__name__)

"""
Mapping helpers to translate FreshService data to Jira data.
Focuses on pure data translation
"""

FRIENDLY_LABELS: Dict[str, str] = settings.FRIENDLY_LABELS
DEFAULT_SUPPRESS_KEYS: set[str]   = settings.DEFAULT_SUPPRESS_KEYS
FS_DEPARTMENT_NAME_MAP: dict[int, str] = settings.FS_DEPARTMENT_NAME_MAP
CATEGORY_AGENT_MAP: dict[str, str] = settings.CATEGORY_AGENT_MAP


# --- Field resolution (name → id) --- #
def resolve_ids_from_field_refs(jira_client, field_ref_by_logical: dict[str, str]) -> dict[str, str]:
    """
    Resolve field references to Jira field IDs.

    Uses Jira's /field API to map human-friendly field names to internal IDs.
    If reference field starts with "customfield_", it is used as-is.
    """
    resp = jira_client.create_request("/field", "GET")
    all_fields = resp.json()
    name_to_id = {f.get("name"): f.get("id") for f in all_fields if f.get("name") and f.get("id")}
    resolved: dict[str, str] = {}
    for logical, ref in field_ref_by_logical.items():
        if not ref:
            continue
        if ref.startswith("customfield_"):
            resolved[logical] = ref
        else:
            fid = name_to_id.get(ref)
            if fid:
                resolved[logical] = fid
    return resolved


def resolve_assignee(category: str) -> str:
    """
    Resolve the Jira assignee based on FreshService ticket category.

    Matches category substrings against keys in CATEGORY_AGENT_MAP.
    Returns the mapped assignee or a default if no match is found.
    """
    for key in CATEGORY_AGENT_MAP:
        if key != "default" and key.lower() in (category or "").lower():
            return CATEGORY_AGENT_MAP[key]
    return CATEGORY_AGENT_MAP.get("default")


def resolve_assignee_by_category_sub(mapping: Mapping, category: Optional[str], subcategory: Optional[str]) -> Optional[str]:
    """
    Resolve assignee by (Category, Subcategory) mapping from mappings.json.

    Precedence:
      1) CATEGORY_SUBCATEGORY_AGENT_MAP[Category][Subcategory]
      2) CATEGORY_SUBCATEGORY_AGENT_MAP[Category]["default"] if present
      3) CATEGORY_AGENT_MAP (category-only fallback)
      4) CATEGORY_AGENT_MAP["default"] if no match
    All matching is case-insensitive on keys.
    """
    try:
        sub_map_by_cat = mapping.category_subcategory_agent_map or {}
    except Exception:
        sub_map_by_cat = {}

    def _norm(s: Optional[str]) -> str:
        return (s or "").strip().lower()

    cat = category or ""
    sub = subcategory or ""

    # Find matching category key (case-insensitive)
    matched_cat_key = None
    for k in sub_map_by_cat.keys():
        if _norm(k) == _norm(cat):
            matched_cat_key = k
            break

    if matched_cat_key is not None:
        sub_map = sub_map_by_cat.get(matched_cat_key) or {}
        # Try exact subcategory match (case-insensitive)
        for sk, assignee in sub_map.items():
            if sk != "default" and _norm(sk) == _norm(sub):
                return assignee
        # Fallback to per-category default if present
        if "default" in sub_map:
            return sub_map.get("default")

    # Fallback to category-only resolver
    return resolve_assignee(cat)


# --- Friendly rendering helpers --- #
def _get_value(obj: Any, path: str) -> Any:
    """Get nested value from object."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
    return cur


def _map_by_str_code(code: Optional[int], m: Dict[str, str]) -> str:
    if code is None:
        return ""
    return m.get(str(code), str(code))



def _format_friendly_value(key: str, raw: Any, mapping: Mapping) -> Optional[str]:
    """Format raw value for friendly display."""
    if raw in (None, "", []):
        return None
    val = raw.strip() if isinstance(raw, str) else raw
    if val in ("", None):
        return None

    if key == "source":
        try:
            return _map_by_str_code(int(val), mapping.fs_source_name_map or {})
        except Exception:
            return str(val)
    if key == "status":
        m = mapping.fs_status_name_map or {}
        return m.get(str(val), str(val))
    if key == "urgency":
        try:
            return _map_by_str_code(int(val), mapping.fs_urgency_name_map or {})
        except Exception:
            return str(val)
    if key == "impact":
        try:
            return _map_by_str_code(int(val), mapping.fs_impact_name_map or {})
        except Exception:
            return str(val)
    if key == "department_id":
        try:
            return _fs_department_name(int(val))
        except Exception:
            return str(val)
    if key == "group_id":
        return str(val)
    return str(val)




def collect_friendly_pairs_for_ticket(
    t: FreshTicket,
    mapping: Mapping,
    suppress_keys: Optional[set[str]] = None,
) -> list[tuple[str, str]]:
    """Extract friendly key-value pairs from a FreshTicket."""
    sup = suppress_keys or DEFAULT_SUPPRESS_KEYS
    candidates = [
        "subject",
        "requester_id",
        "requester.name",
        "requester.phone",
        "requester.location",
    ]
    pairs: list[tuple[str, str]] = []
    for key in candidates:
        if key in sup:
            continue
        raw = _get_value(t, key)
        pretty = _format_friendly_value(key, raw, mapping)
        if pretty is None:
            continue
        label = FRIENDLY_LABELS.get(key, key.replace("_", " ").title())
        pairs.append((label, pretty))
    return pairs


def render_friendly_block(pairs: list[tuple[str, str]], heading: Optional[str] = None) -> str:
    """Build a friendly block of key-value pairs."""
    if not pairs:
        return ""
    lines = []
    if heading:
        lines.append(heading)
        lines.append("-" * len(heading))
    for label, value in pairs:
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def render_ticket_details_comment(t: FreshTicket, mapping: Mapping) -> Optional[str]:
    """Build default "Ticket Details" comment for a given ticket."""
    pairs = collect_friendly_pairs_for_ticket(
        t=t,
        mapping=mapping,
        suppress_keys=DEFAULT_SUPPRESS_KEYS,
    )
    block = render_friendly_block(pairs, heading="Ticket Details")
    return block or None


# --- Issue fields payload --- #
def build_issue_fields(mapping: Mapping, resolved_ids: Dict[str, str], jira_client, t: FreshTicket,
                       project_key: str, issue_type_id: str) -> Dict[str, Any]:
    """
    Build Jira issue fields payload from FreshService ticket.

    - Applies mapping and transformation logic
    - Resolves custom fields.
    - handles fallback and enrichment from requester.
    - Ues Createmeta to validate allowed values.
    """

    def _safe(v):
        return v if v not in (None, "") else None

    # Use _first() to select the first non-empty value from multiple sources
    def _first(*vals):
        for v in vals:
            if v not in (None, ""):
                return v
        return None

    def _map_name_by_code(code: Optional[int], m: Dict[str, str]) -> Optional[str]:
        if code is None:
            return None
        return m.get(str(code), str(code))

    desc_raw = t.description or ""
    desc_text = _strip_html(desc_raw)

    cf = getattr(t, "custom_fields", {}) or {}
    cat = _first(t.category, cf.get("ticket_category"), cf.get("category"), cf.get("category_name"))
    sub = _first(t.sub_category, cf.get("ticket_subcategory"), cf.get("sub_category"),
                 cf.get("user_subcategory"), cf.get("subcategory_name"))
    dept_id = _first(t.department_id, cf.get("department_id"), cf.get("department"))
    business_requestor = _first((t.requester.name if t.requester else None), getattr(t, "name", None))
    location_ticket = _first(
        t.location,
        (t.requester.location if t.requester else None),
        getattr(t, "location", None),
        cf.get("location_ticket"),
        cf.get("location"), cf.get("location_id")
    )
    requestor_phone = _first(
        (t.requester.phone if t.requester else None),
        getattr(t, "phone", None),
        cf.get("requestors_contact_number"),
        cf.get("requestor_phone"),
        cf.get("requester_phone"),
        cf.get("phone_number"),
        cf.get("phone"),
    )

    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "issuetype": {"id": issue_type_id},
        "summary": f"[FS-{t.id}] {t.subject or ''}".strip(),
        "description": jira_client._to_adf(desc_text),
    }

    # Assignee hint via category/subcategory mapping
    assignee_hint = resolve_assignee_by_category_sub(mapping, cat, sub)
    acct_id = jira_client.get_account_id(assignee_hint) if assignee_hint else None
    if acct_id:
        fields["assignee"] = {"accountId": acct_id}

    status_name = (mapping.fs_status_name_map or {}).get(str(t.status), str(t.status))
    urgency_name = _map_name_by_code(t.urgency, mapping.fs_urgency_name_map or {})
    impact_name  = _map_name_by_code(t.impact,  mapping.fs_impact_name_map  or {})
    dept_value   = _fs_department_name(dept_id)
    extracted    = _extract_user_categories_from_description(desc_text)
    ua_cat = _first(t.category, extracted.get("user_assigned_category"))
    ua_sub = _first(t.sub_category, extracted.get("user_assigned_subcategory"))

    pairs = [
        ("fs_ticket_number", str(t.id)),
        ("fs_status", _safe(status_name)),
        ("source",    _safe(_map_name_by_code(t.source, mapping.fs_source_name_map or {})) if t.source is not None else None),
        ("fs_ticket_type", _safe(t.type)),
        ("urgency", _safe(urgency_name)),
        ("impact",  _safe(impact_name)),
        ("group",   _safe(str(t.group_id) if t.group_id is not None else None)),
        ("department", _safe(dept_value)),
        ("ticket_category", _safe(cat)),
        ("ticket_subcategory", _safe(sub)),
        ("business_requestor", _safe(business_requestor)),
        ("requestor_phone", _safe(requestor_phone)),
        ("location_ticket", _safe(str(location_ticket) if isinstance(location_ticket, (int, float)) else location_ticket)),
        ("user_assigned_category", _safe(ua_cat)),
        ("user_assigned_subcategory", _safe(ua_sub)),
    ]

    if dept_value in (None, ""):
        logger.info("FS-%s: Department not present in FS data; Jira field will be omitted.", t.id)

    # Use Createmeta to validate field values before assigning
    fields_meta = _get_fields_meta(jira_client, project_key, issue_type_id)
    for logical_key, raw in pairs:
        fid = resolved_ids.get(logical_key)
        if not fid or raw in (None, ""):
            continue
        logger.debug("Mapping field '%s' → '%s' with raw value: %s", logical_key, fid, raw)

        field_def = fields_meta.get(fid) or {}
        if logical_key == "group":
            fields[fid] = str(raw)
            continue

        _assign_text_or_option(fields, fid, field_def, str(raw))

    return fields



# --- Internal helpers --- #
def _strip_html(text: str) -> str:
    """Convert HTML to plain text."""
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


def _extract_user_categories_from_description(desc_text: str) -> Dict[str, str]:
    """Pull user-assigned category and subcategory from description."""
    result: Dict[str, str] = {}
    patterns = {
        "user_assigned_category": r"(?im)^How\s+the\s+User\s+categorized\s+the\s+issue\s*:\s*(.+)$",
        "user_assigned_subcategory": r"(?im)^The\s+subcategory\s+the\s+User\s+selected\s*:\s*(.+)$",
    }
    for key, pat in patterns.items():
        m = re.search(pat, desc_text)
        if m:
            result[key] = m.group(1).strip()
    return result


def _choose_allowed_value(field_def: Dict[str, Any], desired: str) -> Optional[str]:
    """Choose allowed value from Createmeta."""
    allowed = field_def.get("allowedValues") or []
    d = (desired or "").strip().lower()
    for opt in allowed:
        val = (opt.get("value") or opt.get("name") or "").strip()
        if val.lower() == d:
            return val
    return None

_createmeta_cache: Dict[str, Any] = {}


def _get_fields_meta(jira_client, project_key: str, issue_type_id: str) -> Dict[str, Any]:
    """Get and cache Createmeta for a given project and issue type."""
    cache_key = f"{project_key}:{issue_type_id}"
    if cache_key in _createmeta_cache:
        cm = _createmeta_cache[cache_key]
    else:
        endpoint = (
            f"/issue/createmeta?projectKeys={project_key}"
            f"&issuetypeIds={issue_type_id}"
            f"&expand=projects.issuetypes.fields"
        )
        resp = jira_client.create_request(endpoint, "GET")
        cm = resp.json()
        _createmeta_cache[cache_key] = cm

    projects = cm.get("projects") or [{}]
    issuetypes = projects[0].get("issuetypes") or [{}]
    return issuetypes[0].get("fields", {})

def _fs_impact_name(code: Optional[int]) -> str:
    """Map FreshService impact code to friendly name."""
    mapping = {
        1: "Low",
        2: "Medium",
        3: "High",
    }
    if code is None:
        return ""
    return mapping.get(code, str(code))

def _fs_urgency_name(code: Optional[int]) -> str:
    mapping = {
        1: "Low",
        2: "Medium",
        3: "High"
    }
    if code is None:
        return ""
    return mapping.get(code, str(code))


def _fs_department_name(code: Optional[int]) -> str:
    if code is None:
        return ""
    return FS_DEPARTMENT_NAME_MAP.get(code, str(code))



def _fs_source_name(code: Optional[int]) -> str:
    """Map FreshService source code to friendly name."""
    mapping = {
        1: "Email",
        2: "Portal",
        3: "Phone",
        7: "Chat",
        10: "Outbound Email",
    }
    if code is None:
        return ""
    return mapping.get(code, str(code))


def _assign_text_or_option(fields: dict, fid: str, field_def: dict, desired_name: str) -> None:
    """Populate 'fields[fid]' with 'desired_name' if allowed. Else plain text"""
    if not desired_name:
        return
    schema = field_def.get("schema") or {}
    schema_type = str(schema.get("type") or "").lower()
    has_allowed = bool(field_def.get("allowedValues"))

    if (schema_type == "string") or (not has_allowed):
        fields[fid] = desired_name
        return

    chosen = _choose_allowed_value(field_def, desired_name)
    fields[fid] = {"value": chosen or desired_name}
