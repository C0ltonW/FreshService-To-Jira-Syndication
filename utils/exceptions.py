class SyncError(Exception):
    """Base class for sync-related exceptions"""
    pass

class FreshserviceAPIError(SyncError):
    """Exception for Freshservice API errors"""
    pass

class JiraAPIError(SyncError):
    """Exception for Jira API errors"""
    pass