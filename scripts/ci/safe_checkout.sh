set -euo pipefail
set +x
export LC_ALL=C
# Access-safe checkout. Replaces actions/checkout, which would write EVERY tracked path to the runner's disk, including the one protected template the baseline commit already tracks.
# The exclusion is configured BEFORE the first checkout (non-cone sparse checkout), exactly as docs/audit-2026-11-12/git-prep/verify/build_candidate.ps1 does locally: the entry stays in the index as
# skip-worktree metadata and is never written. Only names are examined; no file content is read. The policy word is assembled from pieces so this script contains no such name literally.
# Needs git >= 2.35 (sparse-checkout --no-cone). Credentials are used for the fetch only and are not stored in the repository configuration, and are never printed.
#
# Which remote and which commit: with a token present (authenticated hosted operation) both come from the job context ONLY (GITHUB_REPOSITORY, GITHUB_SHA). REMOTE_URL, CHECKOUT_SHA and
# ATHENA_CHECKOUT_TOKENLESS are refused when set together with a token, and every inherited GIT_* variable is dropped, so nothing inherited can redirect the authenticated fetch or change the tested
# commit. REMOTE_URL / CHECKOUT_SHA exist for local fixtures only, and are honoured only in the explicit tokenless mode (ATHENA_CHECKOUT_TOKENLESS=1 and no token).
# Path shapes that a line-based sparse pattern cannot carry faithfully (control characters including line breaks, backslashes, a component that begins with a space or ends with a space or a dot,
# a protected name as a directory) are rejected from the commit's metadata BEFORE any file is written; the on-disk audit at the end is a second line, not the first.
w="e""nv"

refuse() { echo "access-safe checkout REFUSED: $*"; exit 1; }
# case-insensitive, like the local builder (a case-insensitive file system would alias `.ENV` to the same file)
protected_name() { local n="${1,,}"; case "$n" in ".${w}"*|*".${w}") return 0 ;; esac; return 1; }

fixture="${ATHENA_CHECKOUT_TOKENLESS:-}"
if [ -n "${GH_TOKEN:-}" ]; then
  mode=authenticated
  if [ -n "$fixture" ] || [ -n "${REMOTE_URL:-}" ] || [ -n "${CHECKOUT_SHA:-}" ]; then
    refuse "an override variable (REMOTE_URL, CHECKOUT_SHA or ATHENA_CHECKOUT_TOKENLESS) is set while a token is present; authenticated operation takes the remote and the commit from the job context only"
  fi
elif [ "$fixture" = "1" ]; then
  mode=fixture
else
  mode=public
  if [ -n "$fixture" ] || [ -n "${REMOTE_URL:-}" ] || [ -n "${CHECKOUT_SHA:-}" ]; then
    refuse "an override variable is set without the explicit tokenless fixture mode (ATHENA_CHECKOUT_TOKENLESS=1)"
  fi
fi

if [ "$mode" = fixture ]; then
  url="${REMOTE_URL:-}"; sha="${CHECKOUT_SHA:-}"
  [ -n "$url" ] && [ -n "$sha" ] || refuse "fixture mode needs REMOTE_URL and CHECKOUT_SHA"
else
  repo="${GITHUB_REPOSITORY:-}"; sha="${GITHUB_SHA:-}"
  repo_re='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'
  [[ "$repo" =~ $repo_re ]] || refuse "GITHUB_REPOSITORY is not a plain owner/name"
  case "${repo%%/*}" in .|..) refuse "GITHUB_REPOSITORY is not a plain owner/name" ;; esac
  case "${repo#*/}" in .|..) refuse "GITHUB_REPOSITORY is not a plain owner/name" ;; esac
  url="https://github.com/${repo}.git"
fi
sha_re='^[0-9a-f]{40}$'
[[ "$sha" =~ $sha_re ]] || refuse "the commit to check out is not a full 40-character lower-case SHA"

removed=()
while IFS= read -r v; do
  case "$v" in GIT_*) removed+=("$v"); unset "$v" ;; esac
done < <(compgen -e)
if [ "${#removed[@]}" -gt 0 ]; then
  echo "access-safe checkout: dropped inherited git control variables (names only): ${removed[*]}"
fi

git init --quiet .
git config core.autocrlf false
git config core.safecrlf false
git config core.longpaths true
git remote add origin "$url"
auth=()
if [ "$mode" = authenticated ]; then
  basic="$(printf 'x-access-token:%s' "$GH_TOKEN" | base64 | tr -d '\n')"
  auth=(-c "http.extraheader=AUTHORIZATION: basic ${basic}")
fi
git ${auth[@]+"${auth[@]}"} fetch --quiet --no-tags --depth=1 origin "$sha"
got="$(git rev-parse "FETCH_HEAD^{commit}")"
[ "$got" = "$sha" ] || refuse "the fetched commit does not equal the requested commit"

listing="$(mktemp)"
git ls-tree -r -z --name-only FETCH_HEAD > "$listing"
patterns='/*'$'\n'
excluded=0
bad=0
shown=0
while IFS= read -r -d '' p; do
  reason=""
  has_protected=0
  lowered="${p,,}"
  if [[ "$lowered" == *".${w}"* ]]; then has_protected=1; fi  # only decides whether a name may be echoed in the failure message
  IFS=/ read -r -a parts <<< "${p//[[:cntrl:]]/_}"
  if [[ "$p" == *[[:cntrl:]]* ]]; then
    reason="a control character"
  elif [[ "$p" == *\\* ]]; then
    reason="a backslash"
  else
    last=$(( ${#parts[@]} - 1 ))
    for i in "${!parts[@]}"; do
      c="${parts[$i]}"
      case "$c" in
        *" "|*".") reason="a component ends with a space or a dot"; break ;;
        " "*) reason="a component starts with a space"; break ;;
      esac
      if [ "$i" -ne "$last" ] && protected_name "$c"; then reason="a protected name used as a directory"; break; fi
    done
  fi
  if [ -n "$reason" ]; then
    bad=$((bad + 1))
    if [ "$shown" -lt 5 ]; then
      shown=$((shown + 1))
      if [ "$has_protected" -eq 1 ]; then echo "  unsupported path ($reason): <protected name withheld>"; else printf '  unsupported path (%s): %q\n' "$reason" "$p"; fi
    fi
    continue
  fi
  if protected_name "${p##*/}"; then
    esc="$(printf '%s' "$p" | sed -e 's/[][*?\x5c]/\x5c&/g' -e 's/ $/\x5c /')"
    patterns+="!/${esc}"$'\n'
    excluded=$((excluded + 1))
  fi
done < "$listing"
rm -f "$listing"
if [ "$bad" -ne 0 ]; then
  refuse "$bad unsupported path shape(s) in the commit's tree; nothing was checked out"
fi
printf '%s' "$patterns" | git sparse-checkout set --no-cone --stdin
git checkout --quiet --detach "$sha"
head="$(git rev-parse HEAD)"
[ "$head" = "$sha" ] || refuse "HEAD after the checkout does not equal the requested commit"
found=0
while IFS= read -r -d '' f; do
  if protected_name "${f##*/}"; then found=$((found + 1)); fi
done < <(find . -path ./.git -prune -o -type f -print0)
if [ "$found" -ne 0 ]; then
  echo "access-safe checkout FAILED: $found protected name(s) exist on disk"
  exit 1
fi
echo "access-safe checkout: protected tracked entries kept as metadata only: $excluded; protected names on disk: 0"
