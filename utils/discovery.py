"""
Discovery utilities for gathering configuration information.

This module provides tools to discover:
- Freshservice agent IDs (by name search)
- Jira user accountIds (by name/email search)
- Jira project issue types
- Freshservice category/subcategory structures
"""

import logging
from typing import Optional, List, Dict
from clients import FreshserviceClient, JiraClient

logger = logging.getLogger(__name__)


def find_freshservice_agent(client: FreshserviceClient, search_term: str) -> List[Dict]:
    """
    Search for Freshservice agents by name or email.

    Args:
        client: FreshserviceClient instance
        search_term: Name or email to search for (case-insensitive)

    Returns:
        List of matching agents with id, name, email
    """
    print(f"\n{'='*80}")
    print(f"Searching for Freshservice agents matching: '{search_term}'")
    print(f"{'='*80}")

    try:
        all_agents = client.get_all_agents(per_page=100, include_inactive=False)
        search_lower = search_term.lower()
        matches = []

        for agent in all_agents:
            agent_id = agent.get("id")
            name = agent.get("name") or ""
            first_name = agent.get("first_name") or ""
            last_name = agent.get("last_name") or ""
            email = agent.get("email") or ""
            contact_email = (agent.get("contact") or {}).get("email") or ""

            full_name = f"{first_name} {last_name}".strip()
            display_name = name or full_name

            # Search in name, email fields
            if (search_lower in display_name.lower() or
                search_lower in email.lower() or
                search_lower in contact_email.lower()):
                matches.append({
                    "id": agent_id,
                    "name": display_name,
                    "email": email or contact_email,
                    "active": agent.get("active", True)
                })

        if matches:
            print(f"\nFound {len(matches)} matching agent(s):\n")
            for match in matches:
                print(f"  Agent ID: {match['id']}")
                print(f"  Name:     {match['name']}")
                print(f"  Email:    {match['email']}")
                print(f"  Active:   {match['active']}")
                print(f"  {'-'*76}")
        else:
            print(f"\n  No agents found matching '{search_term}'")

        return matches

    except Exception as ex:
        logger.exception(f"Failed to search for agents: {ex}")
        return []


def find_jira_user(client: JiraClient, search_term: str) -> List[Dict]:
    """
    Search for Jira users by name or email.

    Args:
        client: JiraClient instance
        search_term: Name or email to search for

    Returns:
        List of matching users with accountId, displayName, emailAddress
    """
    print(f"\n{'='*80}")
    print(f"Searching for Jira users matching: '{search_term}'")
    print(f"{'='*80}")

    try:
        # Use Jira's search API
        resp = client.create_request("/users/search", "GET", params={"query": search_term, "maxResults": 50})
        users = resp.json() or []

        if users:
            print(f"\nFound {len(users)} matching user(s):\n")
            for user in users:
                account_id = user.get("accountId")
                display_name = user.get("displayName")
                email = user.get("emailAddress")
                active = user.get("active", True)

                print(f"  Account ID:    {account_id}")
                print(f"  Display Name:  {display_name}")
                print(f"  Email:         {email}")
                print(f"  Active:        {active}")
                print(f"  {'-'*76}")
        else:
            print(f"\n  No users found matching '{search_term}'")

        return users

    except Exception as ex:
        logger.exception(f"Failed to search for Jira users: {ex}")
        return []


def get_jira_project_issue_types(client: JiraClient, project_key: str) -> List[Dict]:
    """
    Get all issue types for a Jira project.

    Args:
        client: JiraClient instance
        project_key: Jira project key (e.g., "FPA")

    Returns:
        List of issue types with id and name
    """
    print(f"\n{'='*80}")
    print(f"Fetching issue types for Jira project: {project_key}")
    print(f"{'='*80}")

    try:
        issue_types = client.get_issue_types_for_project(project_key)

        if issue_types:
            print(f"\nFound {len(issue_types)} issue type(s):\n")
            for it in issue_types:
                print(f"  ID:   {it['id']}")
                print(f"  Name: {it['name']}")
                print(f"  {'-'*76}")
        else:
            print(f"\n  No issue types found for project '{project_key}'")

        return issue_types

    except Exception as ex:
        logger.exception(f"Failed to fetch issue types for project {project_key}: {ex}")
        print(f"\n  ERROR: Could not fetch issue types. Project may not exist or you may lack permissions.")
        return []


def get_freshservice_categories(client: FreshserviceClient, filter_category: Optional[str] = None) -> None:
    """
    Display Freshservice categories and subcategories.

    Args:
        client: FreshserviceClient instance
        filter_category: Optional category name to filter (e.g., "FP&A")
    """
    print(f"\n{'='*80}")
    if filter_category:
        print(f"Fetching Freshservice categories matching: '{filter_category}'")
    else:
        print(f"Fetching all Freshservice categories and subcategories")
    print(f"{'='*80}")

    try:
        fields = client.get_ticket_fields()

        # Find category field
        category_field = None
        for f in fields or []:
            name = (f.get("name") or "").lower()
            label = (f.get("label") or f.get("label_for_customers") or "").strip().lower()
            if name == "category" or label == "category":
                category_field = f
                break

        if not category_field:
            print("\n  Category field not found in Freshservice ticket fields.")
            return

        def _label_of(option: dict) -> str:
            return (option.get("label") or option.get("value") or option.get("name") or "").strip()

        def _id_of(option: dict):
            return option.get("id") or option.get("value") or option.get("name")

        # Build category → subcategory structure
        categories_found = []
        for top in (category_field.get("choices") or []):
            cat_name = _label_of(top)
            cat_id = _id_of(top)
            if not cat_name:
                continue

            # Apply filter if specified
            if filter_category and filter_category.lower() not in cat_name.lower():
                continue

            subs = top.get("nested_options") or []
            sub_list = []
            for sub in subs:
                sub_name = _label_of(sub)
                sub_id = _id_of(sub)
                if sub_name:
                    sub_list.append({"id": str(sub_id), "name": sub_name})

            categories_found.append({
                "id": str(cat_id),
                "name": cat_name,
                "subcategories": sub_list
            })

        if categories_found:
            print(f"\nFound {len(categories_found)} matching categor(ies):\n")
            for cat in categories_found:
                print(f"Category: {cat['name']} (ID: {cat['id']})")
                if cat['subcategories']:
                    for sub in cat['subcategories']:
                        print(f"  - {sub['name']} (ID: {sub['id']})")
                else:
                    print(f"  (no subcategories)")
                print(f"  {'-'*76}")
        else:
            print(f"\n  No categories found" + (f" matching '{filter_category}'" if filter_category else ""))

    except Exception as ex:
        logger.exception(f"Failed to fetch categories: {ex}")


def run_full_discovery(fresh_client: FreshserviceClient, jira_client: JiraClient) -> None:
    """
    Run complete discovery process for FP&A configuration.

    Args:
        fresh_client: FreshserviceClient instance
        jira_client: JiraClient instance
    """
    print("\n" + "="*80)
    print("FRESHSERVICE -> JIRA DISCOVERY TOOL")
    print("="*80)
    print("\nThis tool will help you discover configuration values for FP&A department setup.")
    print("\nSearching for:\n  1. Casey Derringer (Freshservice agent)\n  2. Varun (Jira user)")
    print("  3. Casey Derringer (Jira user)\n  4. FP&A Jira project issue types")
    print("  5. FP&A Freshservice categories\n")

    # 1. Find Casey Derringer in Freshservice
    print("\n" + "#"*80)
    print("STEP 1: Finding Casey Derringer in Freshservice")
    print("#"*80)
    casey_agents = find_freshservice_agent(fresh_client, "Casey Derringer")

    # 2. Find Varun in Jira
    print("\n" + "#"*80)
    print("STEP 2: Finding Varun in Jira")
    print("#"*80)
    varun_users = find_jira_user(jira_client, "Varun")

    # 3. Find Casey Derringer in Jira
    print("\n" + "#"*80)
    print("STEP 3: Finding Casey Derringer in Jira")
    print("#"*80)
    casey_jira_users = find_jira_user(jira_client, "Casey Derringer")

    # 4. Get FP&A project issue types
    print("\n" + "#"*80)
    print("STEP 4: Getting FP&A Jira project issue types")
    print("#"*80)
    fpa_issue_types = get_jira_project_issue_types(jira_client, "FPA")

    # 5. Get FP&A categories from Freshservice
    print("\n" + "#"*80)
    print("STEP 5: Getting FP&A categories from Freshservice")
    print("#"*80)
    get_freshservice_categories(fresh_client, filter_category="FP&A")

    # Summary
    print("\n" + "="*80)
    print("DISCOVERY SUMMARY")
    print("="*80)
    print(f"\nFreshservice Agent (Casey Derringer): {len(casey_agents)} match(es) found")
    if casey_agents:
        print(f"  -> Use agent_id: {casey_agents[0]['id']}")

    print(f"\nJira User (Varun): {len(varun_users)} match(es) found")
    if varun_users:
        print(f"  -> Use accountId or email: {varun_users[0].get('accountId')} / {varun_users[0].get('emailAddress')}")

    print(f"\nJira User (Casey Derringer): {len(casey_jira_users)} match(es) found")
    if casey_jira_users:
        print(f"  -> Use accountId or email: {casey_jira_users[0].get('accountId')} / {casey_jira_users[0].get('emailAddress')}")

    print(f"\nFPA Jira Issue Types: {len(fpa_issue_types)} found")
    if fpa_issue_types:
        print(f"  -> Use issue_type_id: {fpa_issue_types[0]['id']} ({fpa_issue_types[0]['name']})")

    print("\n" + "="*80)
    print("Next step: Update settings/departments.json with the values above")
    print("="*80 + "\n")
