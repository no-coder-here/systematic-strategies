#!/usr/bin/env bash
# Claude Code statusline: model | context % | 5h usage % | 7d usage % | git branch
# Reads the session JSON on stdin (keys: .model, .context_window, .rate_limits, .workspace).

input=$(cat)

DIM=$'\033[2m'; RESET=$'\033[0m'; SEP="${DIM}│${RESET}"
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; BOLD=$'\033[1m'

# Colour a percentage: green < 50, yellow < 80, red >= 80.
pct_colour() {
  if   [ "$1" -ge 80 ]; then printf '%s' "$RED"
  elif [ "$1" -ge 50 ]; then printf '%s' "$YELLOW"
  else                       printf '%s' "$GREEN"
  fi
}

# Tab-separated so values containing spaces (e.g. "Opus 5", paths) stay intact.
IFS=$'\t' read -r model ctx h5 d7 cwd <<<"$(printf '%s' "$input" | jq -r '
  [ (.model.display_name // "?"),
    (.context_window.used_percentage // -1 | floor),
    (.rate_limits.five_hour.used_percentage  // -1 | floor),
    (.rate_limits.seven_day.used_percentage  // -1 | floor),
    (.workspace.current_dir // .cwd // ".")
  ] | @tsv' 2>/dev/null)"

: "${model:=?}" "${ctx:=-1}" "${h5:=-1}" "${d7:=-1}" "${cwd:=.}"
out="${BOLD}${CYAN}${model}${RESET}"

# Context window, then the two rate-limit windows. Omit any the payload lacks.
add_pct() { # label value
  [ "$2" -ge 0 ] 2>/dev/null || return 0
  out+=" ${SEP} ${DIM}$1${RESET} $(pct_colour "$2")$2%${RESET}"
}
add_pct ctx "$ctx"
add_pct 5h  "$h5"
add_pct 7d  "$d7"

# Git branch: short SHA when detached, omitted entirely outside a repo.
branch=$(git -C "$cwd" symbolic-ref --quiet --short HEAD 2>/dev/null) \
  || branch=$(git -C "$cwd" rev-parse --short HEAD 2>/dev/null)
if [ -n "$branch" ]; then
  dirty=""
  [ -n "$(git -C "$cwd" status --porcelain 2>/dev/null | head -1)" ] && dirty="${YELLOW}*${RESET}"
  out+=" ${SEP} ${DIM}⎇${RESET} ${branch}${dirty}"
fi

printf '%s' "$out"
