#!/usr/bin/env bash
# Site data lives on its own branch, not on main.
#
# Everything the pipeline publishes under docs/<league>/data/ changes several times a day. Committing it to main grew
# the repository by megabytes a day, so it lives on the `site-data` branch instead. That branch always holds exactly
# one commit with no parent: each publish replaces it, so its history never grows (GitHub drops the old objects).
#
#   scripts/site_data.sh pull                      put the published data into docs/<league>/data/
#   scripts/site_data.sh push "message" LEAGUE...  publish docs/<league>/data/ for those leagues
#
# A push replaces only the leagues it names and keeps the others as they are on the branch, so two jobs publishing
# different leagues at once don't overwrite each other. If the branch moved during the push, it starts over from the
# new version (up to five tries).
set -euo pipefail

BRANCH=site-data
REMOTE=${SITE_DATA_REMOTE:-origin}
cd "$(git rev-parse --show-toplevel)"

fetch() {
  # prints the branch's commit, or nothing if it doesn't exist yet
  if git fetch -q "$REMOTE" "+refs/heads/$BRANCH:refs/remotes/$REMOTE/$BRANCH" 2>/dev/null; then
    git rev-parse "refs/remotes/$REMOTE/$BRANCH"
  fi
}

case "${1:-}" in
  pull)
    sha=$(fetch)
    if [ -z "$sha" ]; then
      echo "site_data: no $BRANCH branch yet; nothing to pull" >&2
      exit 0
    fi
    git archive "$sha" docs | tar -x
    echo "site_data: pulled $BRANCH ${sha:0:7}"
    ;;
  push)
    msg=${2:?usage: site_data.sh push MESSAGE LEAGUE...}
    shift 2
    [ $# -gt 0 ] || { echo "usage: site_data.sh push MESSAGE LEAGUE..." >&2; exit 2; }
    for lg in "$@"; do
      [[ "$lg" =~ ^[a-z]+$ ]] || { echo "site_data: bad league '$lg'" >&2; exit 2; }
    done
    index=$(mktemp)
    trap 'rm -f "$index"' EXIT
    for attempt in 1 2 3 4 5; do
      old=$(fetch)
      rm -f "$index"
      if [ -n "$old" ]; then GIT_INDEX_FILE=$index git read-tree "$old"; else GIT_INDEX_FILE=$index git read-tree --empty; fi
      for lg in "$@"; do
        GIT_INDEX_FILE=$index git rm -r -q -f --cached --ignore-unmatch "docs/$lg/data"
        if [ -d "docs/$lg/data" ]; then GIT_INDEX_FILE=$index git add -f "docs/$lg/data"; fi
      done
      tree=$(GIT_INDEX_FILE=$index git write-tree)
      if [ -n "$old" ] && [ "$tree" = "$(git rev-parse "$old^{tree}")" ]; then
        echo "site_data: no changes for $*"
        exit 0
      fi
      commit=$(git commit-tree "$tree" -m "$msg")
      if git push -q --force-with-lease="refs/heads/$BRANCH:$old" "$REMOTE" "$commit:refs/heads/$BRANCH"; then
        echo "site_data: pushed $* as ${commit:0:7}"
        exit 0
      fi
      echo "site_data: $BRANCH moved (try $attempt); retrying" >&2
      sleep $((attempt * 3))
    done
    echo "site_data: could not push after 5 tries" >&2
    exit 1
    ;;
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
