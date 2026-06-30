#!/usr/bin/env bash
# Nightly staging roll for the GSF Astra deployment — direct git-push write path.
#
# Each run:
#   1. Finds the newest app image on nvcr.io (built from this repo by
#      .github/workflows/staging-publish-image.yml) and resolves its digest.
#   2. Refreshes the WWI seed image digest.
#   3. Bumps gsf.podAnnotations.rolledAt to now.
#   4. Commits the updated deployment/stg/values.yaml and pushes it to the Astra
#      deploy repo (main). pdx04's ArgoCD auto-syncs (~3 min): the app redeploys on
#      the newest image and the WWI reset -> seed -> ingest hooks re-run, so staging
#      tracks the latest stg build and resets to clean demo data.
#
# Write path: the deploy repo now lives in the gsf group where you have push
# rights, so this commits the edited values.yaml and pushes to main directly
# (the Fusion values-update API can't resolve a DL for the gsf-group repo). This
# never builds images (that is staging-publish-image.yml on GitHub); it only
# re-pins what already exists on nvcr.io.
#
# Requires: bash, curl, jq, yq (v4 / mikefarah), git; SSH push access to the
# deploy repo (gsf group); and env NGC_API_KEY (nvcr.io read access).
#
# Usage:
#   NGC_API_KEY=... deploy/roll-stg.sh             # resolve, edit, commit + push
#   NGC_API_KEY=... deploy/roll-stg.sh --dry-run   # resolve + show values diff, no commit
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

# ── deployment coordinates ───────────────────────────────────────────────
DEPLOY_REPO_SSH="${DEPLOY_REPO_SSH:-ssh://git@gitlab-master.nvidia.com:12051/gsf/gsf-demo-deploy.git}"
DEPLOY_REPO_URL="${DEPLOY_REPO_URL:-https://gitlab-master.nvidia.com/gsf/gsf-demo-deploy}"
VALUES_PATH_IN_REPO="${VALUES_PATH_IN_REPO:-deployment/stg/values.yaml}"
ENVIRONMENT="${ENVIRONMENT:-stg}"

# ── nvcr.io repos (paths under the registry host) ────────────────────────
APP_REPO="nvstaging/gsf/gsf"            # app image (shares this repo with the OCI chart)
SEED_REPO="nvstaging/gsf/gsf-wwi-seed"  # WWI demo-data seed image

: "${NGC_API_KEY:?set NGC_API_KEY (nvcr.io read access)}"
log() { printf '  %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ── nvcr.io registry (curl + jq; portable, no skopeo needed) ─────────────
# Bearer token for pull scope on $1 (repo path). Realm is discovered from the
# /v2/ 401 challenge so we never hard-code NVIDIA's auth host.
_ngc_token() {
	local repo="$1" hdr realm service
	hdr="$(curl -sSI "https://nvcr.io/v2/" | tr -d '\r' | grep -i '^www-authenticate:' || true)"
	realm="$(printf '%s' "$hdr" | sed -n 's/.*realm="\([^"]*\)".*/\1/p')"
	service="$(printf '%s' "$hdr" | sed -n 's/.*service="\([^"]*\)".*/\1/p')"
	curl -fsS -u "\$oauthtoken:${NGC_API_KEY}" --get "${realm:-https://nvcr.io/proxy_auth}" \
		--data-urlencode "service=${service:-nvcr.io}" \
		--data-urlencode "scope=repository:${repo}:pull" |
		jq -r '.token // .access_token // empty'
}

# Newest tag in $1 starting with prefix $2. Lexical sort is chronological
# because the staging tags end in <UTC yyyymmddHHMMSS>.
newest_tag() {
	local repo="$1" pfx="$2" tok
	tok="$(_ngc_token "$repo")" || return 1
	curl -fsS -H "Authorization: Bearer $tok" "https://nvcr.io/v2/${repo}/tags/list" |
		jq -r --arg p "$pfx" '[.tags[]? | select(startswith($p))] | sort | last // ""'
}

# Resolve $1:$2 to its Docker-Content-Digest (sha256:...).
digest_of() {
	local repo="$1" tag="$2" tok
	tok="$(_ngc_token "$repo")" || return 1
	curl -fsSI -H "Authorization: Bearer $tok" \
		-H 'Accept: application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
		"https://nvcr.io/v2/${repo}/manifests/${tag}" |
		tr -d '\r' | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2}'
}

command -v yq >/dev/null || die "yq (v4) not found"
command -v jq >/dev/null || die "jq not found"
command -v curl >/dev/null || die "curl not found"
command -v git >/dev/null || die "git not found"

# ── pull the current values.yaml from the deploy repo (read-only clone) ──
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
log "cloning deploy repo (read-only)…"
git clone --depth 1 "$DEPLOY_REPO_SSH" "$work/repo" >/dev/null 2>&1 || die "clone failed: $DEPLOY_REPO_SSH"
VALUES="$work/repo/$VALUES_PATH_IN_REPO"
[ -f "$VALUES" ] || die "values not found in repo: $VALUES_PATH_IN_REPO"

# ── resolve the newest app image ─────────────────────────────────────────
# App images are tagged stg.<UTC yyyymmddHHMMSS> (see staging-publish-image.yml).
# The constant "stg." prefix selects them by recency and excludes the OCI chart
# tags (<chartver>-stg.*) that share this nvcr.io repo.
app_prefix="stg."
app_tag="$(newest_tag "$APP_REPO" "$app_prefix")" || die "tag listing failed for $APP_REPO"
[ -n "$app_tag" ] || die "no $APP_REPO tags match ${app_prefix}* (build one via staging-publish-image first)"
app_digest="$(digest_of "$APP_REPO" "$app_tag")" || die "digest lookup failed for $APP_REPO:$app_tag"
[ -n "$app_digest" ] || die "empty digest for $APP_REPO:$app_tag"
log "app image -> $app_tag ($app_digest)"

# ── refresh the WWI seed digest (best-effort; tag is usually 'latest') ───
seed_tag="$(yq '.wwi.image.tag' "$VALUES")"
seed_digest="$(digest_of "$SEED_REPO" "$seed_tag" 2>/dev/null || true)"
if [ -n "$seed_digest" ]; then
	log "wwi seed  -> $seed_tag ($seed_digest)"
else
	log "WARN: could not resolve $SEED_REPO:$seed_tag; keeping current seed digest"
fi

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── apply edits ──────────────────────────────────────────────────────────
APP_TAG="$app_tag" APP_DIGEST="$app_digest" ROLLED="$now" yq -i '
	  .gsf.backend.image.tag = strenv(APP_TAG)
	| .gsf.backend.image.digest = strenv(APP_DIGEST)
	| .gsf.ingestion.image.tag = strenv(APP_TAG)
	| .gsf.ingestion.image.digest = strenv(APP_DIGEST)
	| .gsf.podAnnotations.rolledAt = strenv(ROLLED)
' "$VALUES"
if [ -n "$seed_digest" ]; then
	SEED_DIGEST="$seed_digest" yq -i '.wwi.image.digest = strenv(SEED_DIGEST)' "$VALUES"
fi

if [ "$DRY_RUN" = 1 ]; then
	log "----- dry-run: values diff (no commit) -----"
	git -C "$work/repo" --no-pager diff -- "$VALUES_PATH_IN_REPO" >&2 || true
	exit 0
fi

# ── commit + push directly to the deploy repo (gsf group: we have push) ──
# rolledAt always advances, so there is always something to commit -> the WWI
# data is reset every run even when the image digest is unchanged. (The Fusion
# values-update API can't resolve a DL for the gsf-group repo, so we push.)
log "committing + pushing to the deploy repo…"
git -C "$work/repo" commit -aqm "roll stg: $app_tag + data reset $now"
git -C "$work/repo" push -q origin HEAD:main
log "rolled: $app_tag @ $now — pdx04 ArgoCD auto-syncs within ~3 min"
