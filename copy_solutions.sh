#!/usr/bin/env bash
#
# Author: L. Saetta
# Date Last Modified: 2026-11-23
# License: MIT
# Description: Copies the reference solutions from solutions/labN/ into
#     triage/, for the easy path (docs/easy-path.md) or to get past a lab.
#     A file that already exists in triage/ (for example code built with
#     Codex) is not overwritten unless --force is given.
#
# Usage:
#     ./copy_solutions.sh all        # the three labs: the demo is ready to run
#     ./copy_solutions.sh lab1       # triage/tools.py
#     ./copy_solutions.sh lab2       # triage/agent.py
#     ./copy_solutions.sh lab3       # triage/report.py and triage/cli.py
#     ./copy_solutions.sh all --force   # also replace existing files

set -euo pipefail

cd "$(dirname "$0")"

usage() {
    echo "Usage: ./copy_solutions.sh lab1|lab2|lab3|all [--force]"
    exit 1
}

[[ $# -ge 1 && $# -le 2 ]] || usage
force=0
if [[ $# -eq 2 ]]; then
    [[ "$2" == "--force" ]] || usage
    force=1
fi

case "$1" in
    lab1) files=(solutions/lab1/tools.py) ;;
    lab2) files=(solutions/lab2/agent.py) ;;
    lab3) files=(solutions/lab3/report.py solutions/lab3/cli.py) ;;
    all)
        files=(
            solutions/lab1/tools.py
            solutions/lab2/agent.py
            solutions/lab3/report.py solutions/lab3/cli.py
        )
        ;;
    *) usage ;;
esac

skipped=0
for source in "${files[@]}"; do
    target="triage/$(basename "$source")"
    if [[ -e "$target" && $force -eq 0 ]]; then
        echo "kept     $target (already exists)"
        skipped=$((skipped + 1))
        continue
    fi
    cp -- "$source" "$target"
    echo "copied   $source -> $target"
done

if [[ $skipped -gt 0 ]]; then
    echo "Existing files were kept. Add --force to replace them with the solutions."
fi
