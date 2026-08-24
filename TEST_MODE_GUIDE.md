# Test Mode Guide

## Overview

Test mode provides a safe, controlled environment for testing the Freshservice → Jira synchronization framework without affecting production data. It supports both **dry-run** (read-only) and **live sync** (actual API writes) modes.

---

## Configuration

Test mode is controlled by three environment variables in `settings/settings.env`:

### `IS_TEST`

**Type**: `boolean`
**Default**: `false`
**Purpose**: Enables test mode with detailed logging and debugging output

```env
IS_TEST=true
```

When enabled:
- Uses single test ticket instead of all department tickets
- Prints detailed field mappings, department info, and category structures
- Shows Jira field IDs and EditMeta validation
- Runs idempotency checks (second sync pass)

---

### `SYNC_TEST_TICKET`

**Type**: `boolean`
**Default**: `false`
**Purpose**: Controls whether test mode makes actual API changes

```env
SYNC_TEST_TICKET=false   # Dry-run mode (safe, no writes)
SYNC_TEST_TICKET=true    # Live mode (actual API writes)
```

#### Dry-Run Mode (`SYNC_TEST_TICKET=false`)

**Recommended for**:
- Validating field mappings
- Testing category/subcategory assignment logic
- Verifying label generation
- Reviewing what would be sent to Jira/Freshservice
- Debugging before production deployment

**Behavior**:
- ✅ Reads from Freshservice and Jira APIs
- ✅ Shows exact JSON payloads that would be sent
- ✅ Validates field mappings and EditMeta constraints
- ❌ Does **NOT** create/update Jira issues
- ❌ Does **NOT** sync comments/attachments
- ❌ Does **NOT** update Freshservice statuses

**Output**:
```
================================================================================
DRY-RUN: CREATE Jira Issue (FS-12345)
================================================================================
{
  "fs_ticket_id": 12345,
  "operation": "CREATE",
  "fields": {
    "project": {"key": "FPA"},
    "issuetype": {"id": "10001"},
    "summary": "[FS-12345] Test ticket",
    "labels": ["dashboard-not-refreshing"],
    ...
  }
}
================================================================================
```

#### Live Mode (`SYNC_TEST_TICKET=true`)

**Recommended for**:
- Final validation before production
- Testing actual Jira/Freshservice behavior
- Verifying webhooks and integrations

**Behavior**:
- ✅ Creates/updates Jira issues
- ✅ Syncs comments and attachments
- ✅ Updates Freshservice ticket statuses (if configured)
- ⚠️ **Makes real API changes** - use carefully!

---

### `TEST_TICKET_ID`

**Type**: `integer` (optional)
**Default**: Uses `FRESH_TEST_TICKET` if not specified
**Purpose**: Specifies which Freshservice ticket to test

```env
TEST_TICKET_ID=12345
```

**Use cases**:
- Test specific tickets without changing `FRESH_TEST_TICKET`
- Quick iteration during development
- Testing edge cases (missing fields, special characters, etc.)

If not specified, falls back to the legacy `FRESH_TEST_TICKET` setting.

---

## Usage Examples

### Example 1: Safe Dry-Run Testing

**Goal**: Validate FP&A mappings without making changes

**Configuration** (`settings/settings.env`):
```env
IS_TEST=true
SYNC_TEST_TICKET=false
TEST_TICKET_ID=12345
```

**Run**:
```bash
python main.py
```

**Expected Output**:
- ✅ Prints FP&A category/subcategory mappings
- ✅ Shows Jira field resolution
- ✅ Displays JSON payload that would be sent
- ✅ Validates EditMeta constraints
- ❌ No actual changes to Jira or Freshservice

---

### Example 2: Live Testing Single Ticket

**Goal**: Actually sync test ticket to Jira

**Configuration**:
```env
IS_TEST=true
SYNC_TEST_TICKET=true
TEST_TICKET_ID=12345
```

**Run**:
```bash
python main.py
```

**Expected Output**:
- ✅ Creates/updates Jira issue
- ✅ Syncs comments and attachments
- ✅ Runs second pass to test idempotency
- ✅ Shows post-sync validation

**⚠️ Warning**: This will make real API changes!

---

### Example 3: Quick Ticket Iteration

**Goal**: Test multiple tickets quickly during debugging

**Workflow**:
```bash
# Test ticket 12345
TEST_TICKET_ID=12345 python main.py

# Test ticket 67890
TEST_TICKET_ID=67890 python main.py

# Test ticket 11111
TEST_TICKET_ID=11111 python main.py
```

Set `SYNC_TEST_TICKET=false` to dry-run all of them safely.

---

## Test Mode Output

### Standard Information Printed

1. **Mode Banner**
   ```
   ================================================================================
   TEST MODE: DRY-RUN
   ================================================================================
   ⚠️  SYNC_TEST_TICKET=false - No actual API changes will be made
       Set SYNC_TEST_TICKET=true to enable actual syncing
   ================================================================================
   ```

2. **Department/Group Mappings**
   - Freshservice department ID → name
   - Freshservice group ID → name

3. **Category/Subcategory Structure**
   ```
   --- FreshService Categories and Subcategories (ID → Name) ---
   12345: Dashboards - PowerBI
     - 23456: Connection Issue
     - 23457: Dashboard Not Refreshing
     - 23458: Data Missing or Wrong
   ```

4. **Jira Field Mappings**
   ```
   Jira Fields (name -> id):
   FS Ticket Number                 -> customfield_10512
   Business Requestor               -> customfield_10516
   Ticket Category                  -> customfield_10581
   ```

5. **Test Ticket Details**
   ```
   📋 Test Ticket: id=12345 subject=PowerBI not refreshing
   requester_id=67890 status=2
   ```

6. **Dry-Run Payloads** (if `SYNC_TEST_TICKET=false`)
   - Full JSON of what would be sent
   - CREATE vs UPDATE detection
   - Label generation preview
   - Status sync preview

7. **Post-Sync Validation** (if `SYNC_TEST_TICKET=true`)
   - EditMeta field validation
   - Dropped fields (not editable)
   - Second pass idempotency check

---

## Interpreting Dry-Run Output

### CREATE Operation

```json
{
  "operation": "CREATE",
  "fs_ticket_id": 12345,
  "fields": {
    "project": {"key": "FPA"},
    "issuetype": {"id": "10001"},
    "summary": "[FS-12345] Test ticket",
    "description": {...},
    "labels": ["dashboard-not-refreshing"],
    "customfield_10512": "12345",
    "customfield_10581": "Dashboards - PowerBI",
    "assignee": {"accountId": "abc123"}
  }
}
```

**What this means**:
- No existing Jira issue found for FS-12345
- Would create new issue in FPA project
- Subcategory "Dashboard Not Refreshing" → label "dashboard-not-refreshing"
- Would assign to account ID "abc123"

---

### UPDATE Operation

```json
{
  "operation": "UPDATE",
  "issue_key": "FPA-123",
  "fs_ticket_id": 12345,
  "fields": {
    "summary": "[FS-12345] Test ticket (updated)",
    "customfield_10581": "Dashboards - PowerBI",
    "labels": ["dashboard-not-refreshing", "powerbi"]
  },
  "dropped_fields": ["project", "issuetype", "assignee"]
}
```

**What this means**:
- Found existing issue FPA-123 for FS-12345
- Would update only editable fields
- Labels merged: existing "powerbi" + new "dashboard-not-refreshing"
- Immutable fields dropped (project, issuetype, assignee)

---

### Status Sync Preview

```json
[
  {
    "jira_issue": "FPA-123",
    "jira_status": "Done",
    "fs_ticket_id": 12345,
    "fs_status_code": "4"
  }
]
```

**What this means**:
- Jira issue FPA-123 has status "Done"
- Would update Freshservice ticket 12345 to status 4 (Resolved)
- Based on `status_sync_map: {"Done": "4"}` in department config

---

## Common Scenarios

### Scenario 1: Testing New Department Configuration

**Steps**:
1. Add new department to `settings/departments.json`
2. Set `IS_TEST=true`, `SYNC_TEST_TICKET=false`
3. Set `TEST_TICKET_ID` to ticket owned by that department
4. Run and review dry-run output
5. Verify assignment routing, labels, field mappings
6. Set `SYNC_TEST_TICKET=true` and run live test
7. Disable test mode: `IS_TEST=false`

---

### Scenario 2: Debugging Field Mapping Issues

**Problem**: Jira field not populating correctly

**Debug Steps**:
1. Enable test mode: `IS_TEST=true`, `SYNC_TEST_TICKET=false`
2. Review "Jira Fields (name -> id)" output
3. Check "Preview payload (subset)" for the field
4. Look for "DROPPED" warnings
5. If dropped, check Jira EditMeta: field may not be on Edit screen
6. Verify field is in `settings/mappings.json` → `JIRA_FIELDS`

---

### Scenario 3: Validating Assignment Logic

**Goal**: Ensure category/subcategory routes to correct assignee

**Steps**:
1. Set test ticket with specific category/subcategory
2. Run dry-run mode
3. Check assignment in payload:
   ```json
   "assignee": {"accountId": "abc123"}
   ```
4. Cross-reference with `departments.json` → `assignment_map`
5. Verify against "Jira Fields" output to find email → accountId

**Look for**:
```
FS-12345: Using department-specific assignee routing: Dashboards - PowerBI/Dashboard Not Refreshing → varun@example.com
```

---

### Scenario 4: Testing Label Generation

**Goal**: Verify subcategories convert to valid Jira labels

**Input**: Subcategory "Dashboard Not Refreshing"
**Expected**: Label "dashboard-not-refreshing"

**Steps**:
1. Run dry-run mode
2. Check payload for `"labels"` field
3. Verify normalization:
   - Lowercase: ✅
   - Spaces → hyphens: ✅
   - Special chars removed: ✅

**Rules**:
- `"Dashboard Not Refreshing"` → `"dashboard-not-refreshing"`
- `"FP&A - Other"` → `"fpa-other"`
- `"Connection Issue (VPN)"` → `"connection-issue-vpn"`

---

## Troubleshooting

### Issue: Test mode not activating

**Check**:
```env
IS_TEST=true  # Must be lowercase "true"
```

**Common mistakes**:
- `IS_TEST=True` (uppercase T)
- `IS_TEST=yes`
- `IS_TEST=1`

---

### Issue: Dry-run still making changes

**Check**:
```env
SYNC_TEST_TICKET=false  # Must be lowercase "false"
```

If set to anything other than exactly `false`, it defaults to `true`.

---

### Issue: Wrong ticket being tested

**Check order of precedence**:
1. `TEST_TICKET_ID` (highest priority)
2. `FRESH_TEST_TICKET` (fallback)

**Fix**:
```env
TEST_TICKET_ID=12345  # Override
# or
FRESH_TEST_TICKET=12345  # Legacy
```

---

### Issue: "No test ticket found"

**Possible causes**:
1. Ticket doesn't exist in Freshservice
2. Ticket not owned by configured agents
3. Ticket filtered by status (check `FS_STATUS_INCLUDE`)

**Debug**:
```python
# Check if ticket accessible
fresh_client.get_ticket(12345, include_requester=True)
```

---

### Issue: Fields dropped during update

**Meaning**: Fields not editable via Jira Edit screen

**Solutions**:
1. Add fields to Jira Edit screen configuration
2. Remove from sync if not needed
3. Fields may be immutable (project, issuetype, assignee on updates)

---

## Best Practices

### ✅ Always Start with Dry-Run

Before running live sync:
1. Run dry-run mode
2. Review all payloads
3. Validate field mappings
4. Check assignment routing
5. Verify labels

### ✅ Use Dedicated Test Tickets

Create Freshservice test tickets specifically for testing:
- Predictable category/subcategory
- Known requester
- No production impact if synced

### ✅ Test Both CREATE and UPDATE Paths

1. First run: Tests CREATE (no existing Jira issue)
2. Delete Jira issue
3. Second run: Tests CREATE idempotency
4. Third run: Tests UPDATE (existing issue found)

### ✅ Document Your Tests

Keep notes on:
- Which tickets tested
- Expected vs actual behavior
- Any configuration changes made
- Edge cases discovered

### ✅ Clean Up Test Data

After testing:
- Delete test Jira issues
- Close test Freshservice tickets
- Reset `IS_TEST=false` before production

---

## Integration with CI/CD

### Automated Testing Script

```bash
#!/bin/bash
# test_sync.sh

export IS_TEST=true
export SYNC_TEST_TICKET=false

# Test WMS department
export TEST_TICKET_ID=11111
python main.py | tee test_wms.log

# Test FP&A department
export TEST_TICKET_ID=22222
python main.py | tee test_fpna.log

# Validate outputs
grep "DRY-RUN COMPLETE" test_wms.log && echo "✅ WMS test passed"
grep "DRY-RUN COMPLETE" test_fpna.log && echo "✅ FP&A test passed"
```

---

## Future Enhancements

Potential improvements to test mode:

1. **JSON output file**: Save dry-run payloads to file for comparison
2. **Regression testing**: Compare payloads against known-good baseline
3. **Multi-ticket testing**: Test entire department in dry-run mode
4. **Webhook simulation**: Test status sync without actual Jira updates
5. **Field validation**: Automatically check all required fields present

All can be added without breaking existing behavior.

---

## Summary

| Setting | Value | Behavior |
|---------|-------|----------|
| `IS_TEST=false` | N/A | Production mode (all departments) |
| `IS_TEST=true` + `SYNC_TEST_TICKET=false` | **Recommended** | Dry-run (safe, shows what would happen) |
| `IS_TEST=true` + `SYNC_TEST_TICKET=true` | Use carefully | Live test (actual API writes) |

**Key Takeaway**: Always start with dry-run mode (`SYNC_TEST_TICKET=false`) to validate behavior before enabling live sync.

---

**Last Updated**: 2026-08-24
**Version**: 2.0 (Multi-Department Framework)
