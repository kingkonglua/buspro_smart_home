#!/bin/sh
# One-command regression gate for the _work suite.
#
# The test_*.py files are self-contained scripts, not a pytest suite (they each
# bootstrap sys.path and drive themselves).  Running them in ONE process makes
# them contaminate each other through shared global state; running them one
# process per file is the trustworthy signal.  This script does exactly that:
#
#   * runs every test_*.py in its own interpreter, sequentially;
#   * makes the whole gate non-zero if any regression script fails;
#   * classifies test_gaps_*.py as GAP-ANALYSIS, never as a regression failure:
#       rc=0 -> the "gap" (missing capability) is still reproduced;
#       rc=2 -> the gap is NOT reproduced, i.e. already fixed locally (good);
#   * leaves no files behind (scratch logs live in a mktemp dir, removed on
#     exit / interrupt), so it is safe to re-run after Ctrl-C.
#
# Usage:  sh _work/run_all_tests.sh
#         PY=/path/to/python sh _work/run_all_tests.sh
#
# NOTE: `set -e` is deliberately NOT enabled -- we must see every exit code.

WORK_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$WORK_DIR" || exit 1

if [ -n "$PY" ] && [ -x "$PY" ]; then
    :
elif [ -x /tmp/hdl_venv/bin/python ]; then
    PY=/tmp/hdl_venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/buspro_regression.XXXXXX") || exit 1
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM

pass=0
fail=0
total=0
gap_count=0
harness_error=0
failed_list=""
gap_list=""

run_regression() {
    file="$1"
    total=$((total + 1))
    log="$TMP_DIR/${file%.py}.log"
    timeout 900 "$PY" "$file" >"$log" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
        pass=$((pass + 1))
        printf 'PASS  %s\n' "$file"
    else
        fail=$((fail + 1))
        failed_list="$failed_list $file"
        printf 'FAIL  %s  (rc=%s)\n' "$file" "$rc"
        sed 's/^/      | /' "$log" | tail -n 20
    fi
}

run_gap_analysis() {
    file="$1"
    gap_count=$((gap_count + 1))
    log="$TMP_DIR/${file%.py}.log"
    timeout 900 "$PY" "$file" >"$log" 2>&1
    rc=$?
    if [ "$rc" -eq 2 ]; then
        note="gaps NOT reproduced -> already fixed locally"
    elif [ "$rc" -eq 0 ]; then
        note="all listed gaps still reproduced"
    else
        note="UNEXPECTED rc (script did not complete)"
        harness_error=1
    fi
    gap_list="$gap_list $file"
    printf 'GAP-ANALYSIS  %s  (rc=%s: %s)\n' "$file" "$rc" "$note"
}

printf '== Buspro _work regression gate ==\n'
printf 'python: %s\n\n' "$PY"

# Test files in a stable order.  Gap-analysis scripts are routed to their own
# classifier so they can never be mistaken for a regression failure.
for file in test_*.py; do
    [ -f "$file" ] || continue
    case "$file" in
        test_gaps_ha.py|test_gaps_pybuspro.py)
            run_gap_analysis "$file"
            ;;
        *)
            run_regression "$file"
            ;;
    esac
done

printf '\n'
if [ "$gap_count" -gt 0 ]; then
    printf 'GAP-ANALYSIS (%s, not counted as regressions):%s\n' "$gap_count" "$gap_list"
fi
if [ "$harness_error" -ne 0 ]; then
    printf 'HARNESS ERROR: a gap-analysis script exited with an unexpected status\n'
fi
printf 'REGRESSION: %s/%s PASS, %s FAIL\n' "$pass" "$total" "$fail"

if [ "$fail" -ne 0 ] || [ "$harness_error" -ne 0 ]; then
    exit 1
fi
exit 0
