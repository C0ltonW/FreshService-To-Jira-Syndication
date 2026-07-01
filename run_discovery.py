#!/usr/bin/env python3
"""
Discovery script for FP&A configuration.

Run this script to discover:
- Freshservice agent IDs
- Jira user accountIds
- Jira project issue types
- Freshservice categories/subcategories
"""

from clients import JiraClient, FreshserviceClient
from utils.config import settings
from utils.logger import setup_logging
from utils.discovery import run_full_discovery

def main():
    """Run discovery process."""
    setup_logging()

    print("\nInitializing API clients...")
    fresh_client = FreshserviceClient(settings.freshservice_domain, settings.freshservice_api_key)
    jira_client = JiraClient(settings.jira_domain, settings.jira_email, settings.jira_api_token)
    print("[OK] Connected to Freshservice and Jira\n")

    # Run full discovery
    run_full_discovery(fresh_client, jira_client)

if __name__ == "__main__":
    main()
