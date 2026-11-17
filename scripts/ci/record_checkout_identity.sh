#!/usr/bin/env bash
# Records which commit a CI job actually has checked out, as one JSON file that the job uploads with its artifacts: the commit it is on (HEAD), that commit's tree, its parents, the commit the event
# said to test (GITHUB_SHA), and, for a pull request, the head and base commits the event named. For a pull_request event GITHUB_SHA is GitHub's temporary MERGE commit, not the pull request head; it is
# regenerated later and may no longer be fetchable, so its identity has to be written down while the job runs. This script only RECORDS. It never decides, never changes the checkout and uses no token.
#
# Inputs (environment): IDENTITY_DIR (output folder, required), GITHUB_* (set by the runner), PR_HEAD_SHA and PR_BASE_SHA (passed in by the workflow from the event; empty outside pull requests).
# Best effort, clearly labelled when it cannot: an anonymous fetch of the pull request head commit, to compare its tree with the checked-out tree (this works for a public repository and is skipped, with
# the reason recorded, when it cannot be done; it never uses credentials).
set -euo pipefail
set +x
export LC_ALL=C

dir="${IDENTITY_DIR:-}"
[ -n "$dir" ] || { echo "record_checkout_identity: IDENTITY_DIR is not set" >&2; exit 1; }
if command -v cygpath >/dev/null 2>&1; then dir="$(cygpath -u "$dir")"; fi
mkdir -p "$dir"

hex40='^[0-9a-f]{40}$'
json_escape() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
str() { printf '"%s"' "$(json_escape "$1")"; }
opt() { if [ -n "$1" ]; then str "$1"; else printf 'null'; fi; }
yes_no() { if [ "$1" = "1" ]; then printf 'true'; else printf 'false'; fi; }

head="$(git rev-parse HEAD)"
tree="$(git rev-parse 'HEAD^{tree}')"
parents=()
# The parents are read from the commit object itself, which a shallow checkout still has in full ('git rev-list --parents' would show none at the shallow boundary). Only the HEADER is read: it ends at
# the first empty line, and everything after that is the commit message, where a line that looks like "parent <sha>" is text, not a parent.
while IFS= read -r p; do [ -n "$p" ] && parents+=("$p"); done < <(git cat-file commit HEAD | sed -n -e '/^$/q' -e 's/^parent \([0-9a-f]\{40\}\)$/\1/p')
shallow="$(git rev-parse --is-shallow-repository 2>/dev/null || echo unknown)"

github_sha="${GITHUB_SHA:-}"
pr_head="${PR_HEAD_SHA:-}"
pr_base="${PR_BASE_SHA:-}"
is_pr=0
[[ "$pr_head" =~ $hex40 ]] && is_pr=1

head_is_github_sha=0; [ "$head" = "$github_sha" ] && head_is_github_sha=1
github_sha_is_pr_head=0; [ "$is_pr" = "1" ] && [ "$github_sha" = "$pr_head" ] && github_sha_is_pr_head=1
pr_head_is_parent=0; pr_base_is_parent=0
for p in ${parents[@]+"${parents[@]}"}; do
  [ "$p" = "$pr_head" ] && pr_head_is_parent=1
  [ -n "$pr_base" ] && [ "$p" = "$pr_base" ] && pr_base_is_parent=1
done

pr_tree=""; tree_note="not a pull request, or no head commit was given"; tree_equal="null"
if [ "$is_pr" = "1" ]; then
  tree_note="anonymous fetch of the pull request head commit failed or was not possible (private repository, no network, or the commit is not offered); the trees were not compared"
  fetcher=(git fetch --quiet --no-tags --depth=1 origin "$pr_head")
  if command -v timeout >/dev/null 2>&1; then fetcher=(timeout 60 "${fetcher[@]}"); fi
  if GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=true "${fetcher[@]}" >/dev/null 2>&1; then
    pr_tree="$(git rev-parse 'FETCH_HEAD^{tree}')"
    if [ "$pr_tree" = "$tree" ]; then tree_equal="true"; else tree_equal="false"; fi
    tree_note="compared with the tree of the pull request head commit fetched anonymously"
  fi
fi

parents_json="["
sep=""
for p in ${parents[@]+"${parents[@]}"}; do parents_json+="${sep}$(str "$p")"; sep=", "; done
parents_json+="]"

out="$dir/checkout-identity.json"
{
  printf '{\n'
  printf '  "schema": "athena-checkout-identity-1",\n'
  printf '  "recorded_at_utc": %s,\n' "$(str "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
  printf '  "github": {"event_name": %s, "ref": %s, "repository": %s, "run_id": %s, "run_attempt": %s, "job": %s, "sha": %s},\n' \
    "$(opt "${GITHUB_EVENT_NAME:-}")" "$(opt "${GITHUB_REF:-}")" "$(opt "${GITHUB_REPOSITORY:-}")" "$(opt "${GITHUB_RUN_ID:-}")" "$(opt "${GITHUB_RUN_ATTEMPT:-}")" "$(opt "${GITHUB_JOB:-}")" "$(opt "$github_sha")"
  printf '  "pull_request": {"head_sha": %s, "base_sha": %s},\n' "$(opt "$pr_head")" "$(opt "$pr_base")"
  printf '  "git": {"head": %s, "tree": %s, "parents": %s, "parent_count": %s, "shallow": %s},\n' "$(str "$head")" "$(str "$tree")" "$parents_json" "${#parents[@]}" "$(str "$shallow")"
  printf '  "relations": {"head_equals_github_sha": %s, "github_sha_is_pull_request_head": %s, "pull_request_head_is_a_parent": %s, "pull_request_base_is_a_parent": %s},\n' \
    "$(yes_no "$head_is_github_sha")" "$(yes_no "$github_sha_is_pr_head")" "$(yes_no "$pr_head_is_parent")" "$(yes_no "$pr_base_is_parent")"
  printf '  "tree_comparison": {"pull_request_head_tree": %s, "equals_checked_out_tree": %s, "note": %s}\n' "$(opt "$pr_tree")" "$tree_equal" "$(str "$tree_note")"
  printf '}\n'
} > "$out"
cat "$out"
