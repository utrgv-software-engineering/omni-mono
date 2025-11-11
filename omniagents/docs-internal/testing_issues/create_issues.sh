#!/bin/bash
#
# Script to create GitHub issues from markdown files
# Requires: GitHub CLI (gh) - https://cli.github.com/
#
# Usage: bash docs/testing_issues/create_issues.sh
#

set -e

ISSUES_DIR="docs/testing_issues"

# Check if gh is installed
if ! command -v gh &> /dev/null; then
    echo "Error: GitHub CLI (gh) is not installed"
    echo "Install it from: https://cli.github.com/"
    exit 1
fi

# Check if authenticated
if ! gh auth status &> /dev/null; then
    echo "Error: Not authenticated with GitHub CLI"
    echo "Run: gh auth login"
    exit 1
fi

echo "Creating GitHub issues for testing coverage improvements..."
echo ""

# Function to extract title from frontmatter
extract_title() {
    local file=$1
    grep "^title:" "$file" | sed 's/title: "\(.*\)"/\1/'
}

# Function to extract labels from frontmatter
extract_labels() {
    local file=$1
    grep "^labels:" "$file" | sed 's/labels: \[\(.*\)\]/\1/' | tr -d '"' | tr ',' '\n' | tr -d ' '
}

# Function to remove frontmatter from body
remove_frontmatter() {
    local file=$1
    sed '1,/^---$/d' "$file" | sed '1,/^---$/d'
}

# Issue 01
echo "Creating Issue 01: GEPA agent adapter tests..."
TITLE=$(extract_title "$ISSUES_DIR/issue_01_gepa_agent_adapter_tests.md")
LABELS=$(extract_labels "$ISSUES_DIR/issue_01_gepa_agent_adapter_tests.md" | paste -sd "," -)
BODY=$(remove_frontmatter "$ISSUES_DIR/issue_01_gepa_agent_adapter_tests.md")

gh issue create \
  --title "$TITLE" \
  --label "$LABELS" \
  --body "$BODY" \
  && echo "✓ Issue 01 created" || echo "✗ Issue 01 failed"

echo ""

# Issue 02
echo "Creating Issue 02: GEPA judge adapter tests..."
TITLE=$(extract_title "$ISSUES_DIR/issue_02_gepa_judge_adapter_tests.md")
LABELS=$(extract_labels "$ISSUES_DIR/issue_02_gepa_judge_adapter_tests.md" | paste -sd "," -)
BODY=$(remove_frontmatter "$ISSUES_DIR/issue_02_gepa_judge_adapter_tests.md")

gh issue create \
  --title "$TITLE" \
  --label "$LABELS" \
  --body "$BODY" \
  && echo "✓ Issue 02 created" || echo "✗ Issue 02 failed"

echo ""

# Issue 03
echo "Creating Issue 03: SafeAgent tests..."
TITLE=$(extract_title "$ISSUES_DIR/issue_03_safe_agent_tests.md")
LABELS=$(extract_labels "$ISSUES_DIR/issue_03_safe_agent_tests.md" | paste -sd "," -)
BODY=$(remove_frontmatter "$ISSUES_DIR/issue_03_safe_agent_tests.md")

gh issue create \
  --title "$TITLE" \
  --label "$LABELS" \
  --body "$BODY" \
  && echo "✓ Issue 03 created" || echo "✗ Issue 03 failed"

echo ""

# Issue 04
echo "Creating Issue 04: Scenarios execution tests..."
TITLE=$(extract_title "$ISSUES_DIR/issue_04_scenarios_execution_tests.md")
LABELS=$(extract_labels "$ISSUES_DIR/issue_04_scenarios_execution_tests.md" | paste -sd "," -)
BODY=$(remove_frontmatter "$ISSUES_DIR/issue_04_scenarios_execution_tests.md")

gh issue create \
  --title "$TITLE" \
  --label "$LABELS" \
  --body "$BODY" \
  && echo "✓ Issue 04 created" || echo "✗ Issue 04 failed"

echo ""

# Issue 05
echo "Creating Issue 05: RPC server error tests..."
TITLE=$(extract_title "$ISSUES_DIR/issue_05_rpc_server_error_tests.md")
LABELS=$(extract_labels "$ISSUES_DIR/issue_05_rpc_server_error_tests.md" | paste -sd "," -)
BODY=$(remove_frontmatter "$ISSUES_DIR/issue_05_rpc_server_error_tests.md")

gh issue create \
  --title "$TITLE" \
  --label "$LABELS" \
  --body "$BODY" \
  && echo "✓ Issue 05 created" || echo "✗ Issue 05 failed"

echo ""

# Issue 06
echo "Creating Issue 06: Config loader edge cases..."
TITLE=$(extract_title "$ISSUES_DIR/issue_06_config_loader_edge_cases.md")
LABELS=$(extract_labels "$ISSUES_DIR/issue_06_config_loader_edge_cases.md" | paste -sd "," -)
BODY=$(remove_frontmatter "$ISSUES_DIR/issue_06_config_loader_edge_cases.md")

gh issue create \
  --title "$TITLE" \
  --label "$LABELS" \
  --body "$BODY" \
  && echo "✓ Issue 06 created" || echo "✗ Issue 06 failed"

echo ""

# Issue 07
echo "Creating Issue 07: Pipeline judge execution tests..."
TITLE=$(extract_title "$ISSUES_DIR/issue_07_pipeline_judge_execution.md")
LABELS=$(extract_labels "$ISSUES_DIR/issue_07_pipeline_judge_execution.md" | paste -sd "," -)
BODY=$(remove_frontmatter "$ISSUES_DIR/issue_07_pipeline_judge_execution.md")

gh issue create \
  --title "$TITLE" \
  --label "$LABELS" \
  --body "$BODY" \
  && echo "✓ Issue 07 created" || echo "✗ Issue 07 failed"

echo ""
echo "Done! All issues created successfully."
echo ""
echo "View issues at: https://github.com/utrgv-software-engineering/omniagents/issues"
