#!/usr/bin/env bash
# release.sh -- prepare a release PR, then tag it to trigger the Release workflow.
#
# Mirrors the coherence-workstation / coherence-recorder release scripts, but
# adapted to what Refrain needs: `main` is branch-protected here, so the version
# bump CANNOT be pushed straight to main -- it goes through a PR. Hence two steps
# instead of one push:
#
#   ./scripts/release.sh 0.7.0                 # bump versions + CHANGELOG, open the release PR
#   ./scripts/release.sh 0.7.0 --tag           # AFTER the PR merges: tag v0.7.0 + push
#   ./scripts/release.sh 0.7.0 --tag --rebuild # delete + re-create the tag at origin/main
#
# The 'v' prefix is added automatically -- pass just the number.
#
# Pushing the tag triggers .github/workflows/release.yml, which builds the
# refrain_core wheels (per OS/arch), the pure-Python refrain wheel + sdist, and
# the iOS xcframework + Android JNI bundle, and attaches them to the GitHub
# Release for that tag.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BOLD='\033[1m'; RESET='\033[0m'
die()  { echo -e "${RED}Error:${RESET} $1" >&2; exit 1; }
info() { echo -e "${GREEN}ok${RESET} $1"; }

# -- Version sources --------------------------------------------------------
# Both feed the published wheels (maturin reads refrain-core/pyproject.toml; the
# pure-Python wheel reads the root pyproject.toml). refrain-core/Cargo.toml holds
# the independent *crate* version and is intentionally NOT bumped here.
PYPROJECTS=("pyproject.toml" "refrain-core/pyproject.toml")
CHANGELOG="CHANGELOG.md"
BASE="main"

# -- Parse arguments --------------------------------------------------------
TAG_STEP=false; REBUILD=false; VERSION=""
for arg in "$@"; do
    case "$arg" in
        --tag)     TAG_STEP=true ;;
        --rebuild) REBUILD=true ;;
        *) [[ -z "$VERSION" ]] || die "Unexpected extra argument: '$arg' (one version only)"; VERSION="$arg" ;;
    esac
done

if [[ -z "$VERSION" ]]; then
    echo -e "${BOLD}Usage:${RESET} ./scripts/release.sh <version> [--tag] [--rebuild]"
    echo ""
    echo "  ./scripts/release.sh 0.7.0           # open the release PR (bump + CHANGELOG)"
    echo "  ./scripts/release.sh 0.7.0 --tag     # after merge: tag + push -> Release workflow"
    echo "  ./scripts/release.sh 0.7.0 --tag --rebuild   # re-tag at origin/main and re-push"
    echo ""
    echo "  The 'v' prefix is added automatically -- pass just the number."
    exit 1
fi

VERSION="${VERSION#v}"
TAG="v${VERSION}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
    die "Invalid version '${VERSION}'. Expected semver like 0.7.0 or 0.7.0-beta.1"
fi
[[ "$REBUILD" == true && "$TAG_STEP" == false ]] && die "--rebuild only applies to the --tag step."

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "Not in a git repository"
cd "$REPO_ROOT"

# Bump the [project] version in a pyproject file. count=1 rewrites the FIRST
# line-anchored `version = "..."` -- that's the [project] version because
# [build-system] (which precedes it) has no `version =` key. Mirrors the CR
# script's idiom; if a `version =` is ever added to an earlier table, make this
# [project]-section-aware.
bump_pyproject() {
    python3 -c "import re,pathlib,sys;p=pathlib.Path(sys.argv[1]);t=p.read_text();t=re.sub(r'^(version\s*=\s*\").+?\"', r'\g<1>${VERSION}\"', t, count=1, flags=re.MULTILINE);p.write_text(t)" "$1"
}
pyproject_version() { sed -n 's/^version = "\([^"]*\)"/\1/p' "$1" | head -1; }

# ===========================================================================
# Step 2: --tag  (run after the prepare PR has merged)
# ===========================================================================
if [[ "$TAG_STEP" == true ]]; then
    git fetch origin "$BASE" --tags --quiet

    if git rev-parse "$TAG" >/dev/null 2>&1; then
        [[ "$REBUILD" == true ]] || die "Tag '${TAG}' already exists. Use --rebuild to retrigger, or pick a new version."
        echo -e "${YELLOW}Tag '${TAG}' exists -- will delete and re-create (--rebuild)${RESET}"
    fi

    # The prepare PR must have landed: origin/main has to carry <version>.
    for f in "${PYPROJECTS[@]}"; do
        got="$(git show "origin/${BASE}:$f" | sed -n 's/^version = "\([^"]*\)"/\1/p' | head -1)"
        [[ "$got" == "$VERSION" ]] || die "origin/${BASE} ${f} is at '${got}', not '${VERSION}'. Merge the release PR first (./scripts/release.sh ${VERSION})."
    done
    git show "origin/${BASE}:${CHANGELOG}" | grep -q "^## \[${VERSION}\]" \
        || die "origin/${BASE} ${CHANGELOG} has no '## [${VERSION}]' heading. Merge the release PR first."
    info "origin/${BASE} carries ${VERSION}"

    echo ""
    echo -e "${BOLD}Tag release ${VERSION}${RESET} (tag ${TAG} at origin/${BASE})"
    echo "  Pushing the tag triggers the Release workflow (wheels + mobile artifacts)."
    read -rp "Proceed? [y/N] " CONFIRM
    [[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

    if [[ "$REBUILD" == true ]]; then
        git tag -d "$TAG" 2>/dev/null || true
        git push origin ":refs/tags/$TAG" --quiet 2>/dev/null || true
    fi
    git tag -a "$TAG" -m "Release ${TAG}" "origin/${BASE}"
    git push origin "$TAG" --quiet
    info "Pushed ${TAG} -- Release workflow triggered"
    echo ""
    echo -e "${GREEN}${BOLD}Done.${RESET}"
    echo "  Watch:    gh run list --workflow=release.yml"
    echo "  Releases: https://github.com/refrain-lang/refrain/releases"
    exit 0
fi

# ===========================================================================
# Step 1: prepare  (open the release PR)
# ===========================================================================
command -v gh >/dev/null || die "gh (GitHub CLI) not found -- needed to open the release PR."
gh auth status >/dev/null 2>&1 || die "gh is not authenticated (run: gh auth login)."

if ! git diff --quiet || ! git diff --cached --quiet; then
    die "Working tree is not clean. Commit or stash your changes first."
fi

BRANCH="release-v${VERSION}"
git fetch origin "$BASE" --tags --quiet

git rev-parse "$TAG" >/dev/null 2>&1 && die "Tag '${TAG}' already exists -- pick a new version."
git ls-remote --exit-code --tags origin "$TAG" >/dev/null 2>&1 && die "Tag '${TAG}' already exists on origin."
git show-ref -q --verify "refs/heads/${BRANCH}" && die "Branch '${BRANCH}' already exists locally (delete it or pick another version)."

CUR_VERSION="$(git show "origin/${BASE}:pyproject.toml" | sed -n 's/^version = "\([^"]*\)"/\1/p' | head -1)"
info "Preflight passed"

echo ""
echo -e "${BOLD}Release Summary${RESET}"
echo "  Current version (origin/${BASE}):  ${CUR_VERSION}"
echo "  New version:                       ${VERSION}"
echo "  Release branch:                    ${BRANCH}  ->  PR into ${BASE}"
echo "  Tag (after merge):                 ${TAG}  (run: ./scripts/release.sh ${VERSION} --tag)"
echo ""
echo "  This will, off a fresh origin/${BASE}:"
echo "    1. Bump version -> ${VERSION} in ${PYPROJECTS[*]}"
echo "    2. Finalize the top CHANGELOG '## [Unreleased]' -> '## [${VERSION}] — $(date +%F)'"
echo "    3. Commit, push '${BRANCH}', and open a PR"
echo ""
read -rp "Proceed? [y/N] " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

info "Creating ${BRANCH} from origin/${BASE}"
git checkout -q -b "$BRANCH" "origin/${BASE}"

for f in "${PYPROJECTS[@]}"; do
    [[ -f "$f" ]] || die "missing ${f}"
    bump_pyproject "$f"
    [[ "$(pyproject_version "$f")" == "$VERSION" ]] || die "failed to bump version in ${f}."
    info "Updated ${f} to ${VERSION}"
done

# Finalize ONLY the first '## [Unreleased]' (a stale lower one, if any, is left
# for a maintainer to fold in -- we never rewrite released history).
grep -q '^## \[Unreleased\]$' "$CHANGELOG" || die "no '## [Unreleased]' heading in ${CHANGELOG} -- add release notes there first."
awk -v repl="## [${VERSION}] — $(date +%F)" '
    !done && /^## \[Unreleased\]$/ { print repl; done=1; next }
    { print }
' "$CHANGELOG" > "$CHANGELOG.tmp" && mv "$CHANGELOG.tmp" "$CHANGELOG"
info "Finalized ${CHANGELOG}: ## [${VERSION}] — $(date +%F)"
[[ "$(grep -c '^## \[Unreleased\]$' "$CHANGELOG")" -gt 0 ]] \
    && echo -e "${YELLOW}note:${RESET} another '## [Unreleased]' heading remains in ${CHANGELOG} (pre-existing; not touched)."

git commit -q -am "release: v${VERSION}"
git push -q -u origin "$BRANCH"
info "Pushed ${BRANCH}"

gh pr create --base "$BASE" --head "$BRANCH" \
    --title "release: v${VERSION}" \
    --body "Release prep for **v${VERSION}**: bump the version in ${PYPROJECTS[*]} and finalize the CHANGELOG.

After merge, tag the release to trigger \`release.yml\`:
\`\`\`
./scripts/release.sh ${VERSION} --tag
\`\`\`"

echo ""
echo -e "${GREEN}${BOLD}Done.${RESET} Review & merge the PR, then run:  ./scripts/release.sh ${VERSION} --tag"
