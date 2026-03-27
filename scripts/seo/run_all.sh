#!/bin/bash
# highlevel.ai SEO Automation Suite
# Usage: ./scripts/seo/run_all.sh [--daily|--weekly|--full]

set -o pipefail

# ──────────────────────────────────────────────
# Colors
# ──────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ──────────────────────────────────────────────
# Project root (two levels up from this script)
# ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT" || { echo -e "${RED}ERROR: Cannot cd to project root: $PROJECT_ROOT${NC}"; exit 1; }

# ──────────────────────────────────────────────
# Mode parsing
# ──────────────────────────────────────────────
MODE="daily"
if [[ "$1" == "--weekly" ]]; then
    MODE="weekly"
elif [[ "$1" == "--full" ]]; then
    MODE="full"
elif [[ "$1" == "--daily" ]] || [[ -z "$1" ]]; then
    MODE="daily"
elif [[ -n "$1" ]]; then
    echo -e "${RED}Unknown option: $1${NC}"
    echo "Usage: $0 [--daily|--weekly|--full]"
    echo "  --daily   (default) Fast, safe to run daily"
    echo "  --weekly  Includes API calls and deeper analysis"
    echo "  --full    Everything including HTML modification dry-runs"
    exit 1
fi

# ──────────────────────────────────────────────
# Dependency checks
# ──────────────────────────────────────────────
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "${RED}ERROR: Required command '$1' not found.${NC}"
        exit 1
    fi
}

check_command python3
if [[ "$MODE" == "full" ]]; then
    check_command node
fi

# ──────────────────────────────────────────────
# Virtual environment activation
# ──────────────────────────────────────────────
if [[ -f "$PROJECT_ROOT/venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/venv/bin/activate"
    echo -e "${CYAN}Activated virtual environment: $PROJECT_ROOT/venv${NC}"
elif [[ -f "$PROJECT_ROOT/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
    echo -e "${CYAN}Activated virtual environment: $PROJECT_ROOT/.venv${NC}"
fi

# ──────────────────────────────────────────────
# Task runner
# ──────────────────────────────────────────────
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
declare -a RESULTS=()

run_task() {
    local task_name="$1"
    shift
    local cmd="$*"

    echo ""
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}▶ ${task_name}${NC}"
    echo -e "  ${cmd}"
    echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    local start_time
    start_time=$(date +%s)

    eval "$cmd"
    local exit_code=$?

    local end_time
    end_time=$(date +%s)
    local duration=$(( end_time - start_time ))
    local minutes=$(( duration / 60 ))
    local seconds=$(( duration % 60 ))
    local time_str="${minutes}m ${seconds}s"

    if [[ $exit_code -eq 0 ]]; then
        echo -e "${GREEN}✓ ${task_name} completed (${time_str})${NC}"
        RESULTS+=("${GREEN}✓${NC} ${task_name} — ${time_str}")
        (( PASS_COUNT++ ))
    else
        echo -e "${RED}✗ ${task_name} failed (exit code $exit_code, ${time_str})${NC}"
        RESULTS+=("${RED}✗${NC} ${task_name} — exit code $exit_code (${time_str})")
        (( FAIL_COUNT++ ))
    fi
}

# ──────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   highlevel.ai SEO Automation Suite              ║${NC}"
echo -e "${BOLD}║   Mode: ${CYAN}${MODE}${NC}${BOLD}$(printf '%*s' $((39 - ${#MODE})) '')║${NC}"
echo -e "${BOLD}║   $(date '+%Y-%m-%d %H:%M:%S')$(printf '%*s' 31 '')║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"

SUITE_START=$(date +%s)

# ──────────────────────────────────────────────
# Daily tasks
# ──────────────────────────────────────────────
run_task "Freshness Monitor (skip links)" \
    "python3 scripts/seo/freshness_monitor.py --skip-links"

run_task "Sitemap Updater" \
    "python3 scripts/seo/update_sitemap.py"

# ──────────────────────────────────────────────
# Weekly tasks (also included in --full)
# ──────────────────────────────────────────────
if [[ "$MODE" == "weekly" || "$MODE" == "full" ]]; then
    # Re-run freshness monitor with full link checking (replaces the skip-links run)
    run_task "Freshness Monitor (full)" \
        "python3 scripts/seo/freshness_monitor.py"

    run_task "Schema Audit" \
        "python3 scripts/seo/schema_audit.py --audit"

    run_task "SEO Dashboard (7-day)" \
        "python3 scripts/seo/seo_dashboard.py --days 7"

    run_task "LLM Visibility Tracker" \
        "python3 scripts/seo/llm_visibility_tracker.py --run"
fi

# ──────────────────────────────────────────────
# Full tasks (dry-run only — apply manually)
# ──────────────────────────────────────────────
if [[ "$MODE" == "full" ]]; then
    run_task "Internal Links (dry-run)" \
        "node scripts/seo/auto_internal_links.js --dry-run"

    run_task "Page Generator (validate)" \
        "python3 scripts/seo/generate_pages.py --validate"

    echo ""
    echo -e "${YELLOW}NOTE: To apply changes from Internal Links or Page Generator,${NC}"
    echo -e "${YELLOW}run them manually with --apply after reviewing the dry-run output.${NC}"
fi

# ──────────────────────────────────────────────
# Daily Summary
# ──────────────────────────────────────────────
run_task "Daily Summary" \
    "python3 scripts/seo/daily_summary.py"

# ──────────────────────────────────────────────
# Final summary
# ──────────────────────────────────────────────
SUITE_END=$(date +%s)
SUITE_DURATION=$(( SUITE_END - SUITE_START ))
SUITE_MIN=$(( SUITE_DURATION / 60 ))
SUITE_SEC=$(( SUITE_DURATION % 60 ))

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   Run Complete                                   ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Mode:     ${CYAN}${MODE}${NC}"
echo -e "  Duration: ${SUITE_MIN}m ${SUITE_SEC}s"
echo -e "  Passed:   ${GREEN}${PASS_COUNT}${NC}"
echo -e "  Failed:   ${RED}${FAIL_COUNT}${NC}"
echo ""
echo -e "${BOLD}Task Results:${NC}"
for result in "${RESULTS[@]}"; do
    echo -e "  ${result}"
done
echo ""

# ──────────────────────────────────────────────
# Generate dashboard data (always runs last)
# ──────────────────────────────────────────────
echo ""
echo -e "${CYAN}→ Generating dashboard data...${NC}"
python3 "${SCRIPT_DIR}/generate_dashboard_data.py" && \
    echo -e "  ${GREEN}✓ Dashboard data updated${NC}" || \
    echo -e "  ${RED}✗ Dashboard data generation failed${NC}"

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo -e "${YELLOW}Some tasks failed. Check output above for details.${NC}"
    exit 1
else
    echo -e "${GREEN}All tasks completed successfully.${NC}"
    exit 0
fi
