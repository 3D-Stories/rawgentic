#!/bin/bash
# Test suite for wal-guard hook patterns
set -euo pipefail

GUARD="./hooks/wal-guard"
PASS=0
FAIL=0

test_cmd() {
  local label="$1" cmd="$2" expected="$3"
  local input
  input=$(jq -nc --arg cmd "$cmd" '{"tool_input":{"command":$cmd}}')
  local result
  result=$(echo "$input" | bash "$GUARD" 2>/dev/null) || true
  if [ -z "$result" ]; then
    actual="ALLOW"
  else
    actual="DENY"
  fi
  if [ "$actual" = "$expected" ]; then
    echo "  PASS [$actual] $label"
    PASS=$((PASS + 1))
  else
    echo "  FAIL [$actual expected $expected] $label"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== AC1-3: Local file/git/gh operations should ALLOW ==="
test_cmd "git diff deploy-prod file" 'git diff .github/workflows/deploy-prod.yml' "ALLOW"
test_cmd "git commit with deploy-prod msg" 'git commit -m "fix deploy-prod workflow"' "ALLOW"
test_cmd "git log grep deploy-prod" 'git log --grep="deploy-prod"' "ALLOW"
test_cmd "git add deploy-prod file" 'git add .github/workflows/deploy-prod.yml' "ALLOW"
test_cmd "gh pr create with deploy-prod" 'gh pr create --body "updates deploy-prod pipeline"' "ALLOW"
test_cmd "gh issue create deploy-prod" 'gh issue create --body "deploy-prod bug"' "ALLOW"
test_cmd "cat deploy-prod file" 'cat .github/workflows/deploy-prod.yml' "ALLOW"
test_cmd "sed on deploy-prod file" "sed -i 's/old/new/' deploy-prod.yml" "ALLOW"
test_cmd "GH_TOKEN export + gh pr" 'export GH_TOKEN=abc && gh pr create --body "deploy-prod"' "ALLOW"
test_cmd "git checkout branch with deploy-prod" 'git checkout -b fix/11-wal-guard-deploy-prod-pattern' "ALLOW"

echo ""
echo "=== AC4: Actual deployment commands should DENY ==="
test_cmd "ssh to prod host" 'ssh user@prod-host' "DENY"
test_cmd "ssh to staging.prod.example" 'ssh user@staging.prod.example.com uptime' "DENY"
test_cmd "scp to prod" 'scp build.tar.gz prod-server:/opt/app/' "DENY"
test_cmd "rsync to prod" 'rsync -avz ./dist/ prod-host:/var/www/' "DENY"
test_cmd "docker compose prod up" 'docker compose -f docker-compose.prod.yml up -d' "DENY"
test_cmd "kubectl prod context" 'kubectl --context prod-cluster apply -f deploy.yml' "DENY"
test_cmd "terraform apply prod" 'terraform apply -var-file=prod.tfvars' "DENY"
test_cmd "ansible prod" 'ansible-playbook -i prod-inventory site.yml' "DENY"
test_cmd "helm install prod" 'helm install myapp ./chart --set env=prod' "DENY"
test_cmd "ssh with compose prod up" 'ssh root@10.0.17.202 "docker compose -f /srv/app/docker-compose.sdlc.prod.yml up -d"' "DENY"

echo ""
echo "=== Existing patterns still work ==="
test_cmd "rm -rf" 'rm -rf /tmp/test' "DENY"
test_cmd "git push --force" 'git push --force origin main' "DENY"
test_cmd "git reset --hard" 'git reset --hard HEAD~1' "DENY"
test_cmd "git commit --no-verify" 'git commit --no-verify -m "skip hooks"' "DENY"
test_cmd "normal git push (safe)" 'git push origin feature-branch' "ALLOW"
test_cmd "normal rm (safe)" 'rm file.txt' "ALLOW"

echo ""
echo "=== Edge cases ==="
test_cmd "docker compose prod config (read-only)" 'docker compose -f docker-compose.prod.yml config' "ALLOW"
test_cmd "docker compose prod ps (read-only)" 'docker compose -f docker-compose.prod.yml ps' "ALLOW"
test_cmd "echo deploy prod (not a real command)" 'echo "deploy to prod"' "ALLOW"
test_cmd "env var with prod (no deploy tool)" 'export DEPLOY_ENV=prod && echo done' "ALLOW"

echo ""
echo "==============================="
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "ALL TESTS PASSED" || echo "SOME TESTS FAILED"
exit "$FAIL"
