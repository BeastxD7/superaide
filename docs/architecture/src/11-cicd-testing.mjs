import { Diagram } from './lib.mjs'

const d = new Diagram({
  name: '11-cicd-testing',
  title: 'CI/CD, testing and hosting',
  subtitle: 'A pull request runs only the tests for the modules it touches and their dependents; main and a two-hourly Full run run everything.',
  height: 1100,
})
d.zone('dev', { x: 40, y: 120, w: 330, h: 480, label: 'Contributors', color: 'blue', icon: 'lucide:users' })
d.zone('ci', { x: 410, y: 120, w: 1060, h: 480, label: 'GitHub Actions: ci.yml', color: 'green', icon: 'brand:githubactions' })
d.zone('main', { x: 1510, y: 120, w: 370, h: 480, label: 'Main and hosting', color: 'amber', icon: 'lucide:server' })
d.zone('tests', { x: 40, y: 630, w: 1100, h: 380, label: 'What the tests cover', color: 'violet', icon: 'lucide:flask-conical' })
d.zone('gate', { x: 1180, y: 630, w: 700, h: 380, label: 'Merge gates', color: 'rose', icon: 'lucide:shield-check' })

d.node('dv', { x: 62, y: 170, w: 286, h: 100, icon: 'lucide:terminal', title: 'Branch and pull request', sub: 'Humans and agents; agents never merge. Merge commits, no squash', color: 'blue' })
d.node('hk', { x: 62, y: 310, w: 286, h: 110, icon: 'lucide:check', title: 'make check locally', sub: 'ruff, mypy, check-env, tests of touched modules, frontend lint, types, build', color: 'blue' })
d.node('gh', { x: 62, y: 460, w: 286, h: 110, icon: 'brand:github', title: 'PR template and labels', sub: 'Security checklist, flags, rollback plan, before and after screenshots', color: 'blue' })
d.edge('dv', 'hk', { from: 'b', to: 't' }); d.edge('hk', 'gh', { from: 'b', to: 't' })

d.node('c0', { x: 432, y: 170, w: 330, h: 110, icon: 'lucide:git-branch', title: 'changes', sub: 'select_tests.py reads ci/modules.toml and the import graph. Any doubt, or a pipeline file, selects everything', color: 'green', accent: '#34a853' })
d.node('c1', { x: 802, y: 170, w: 300, h: 110, icon: 'brand:python', title: 'backend-static', sub: 'one alembic head, ruff, mypy, alembic check and downgrade round trip', color: 'green' })
d.node('c2', { x: 1142, y: 170, w: 300, h: 110, icon: 'brand:pytest', title: 'backend-tests', sub: '2 shards, Postgres 17; on a PR only the selected files', color: 'green' })
d.node('c3', { x: 432, y: 320, w: 330, h: 110, icon: 'brand:react', title: 'frontend', sub: 'npm ci, oxlint, tsc, vitest on selected files, next build', color: 'green' })
d.node('c4', { x: 802, y: 320, w: 300, h: 110, icon: 'lucide:play', title: 'e2e (Playwright)', sub: '4 shards on isolated stacks. Advisory on PRs unless labelled e2e-blocking', color: 'green', accent: '#f2a900' })
d.node('c5', { x: 1142, y: 320, w: 300, h: 110, icon: 'brand:docker', title: 'images', sub: 'prod compose config, builds api, web, caddy; caddy validate', color: 'green' })
d.node('c6', { x: 432, y: 470, w: 330, h: 100, icon: 'lucide:ban', title: 'secrets and tests-required', sub: 'gitleaks; a feat or fix must change tests too', color: 'green' })
d.node('c7', { x: 802, y: 470, w: 640, h: 100, icon: 'lucide:badge-check', title: 'CI result: the one required check', sub: 'Skipped jobs count as passed unless the filter asked for them. e2e failure is non-blocking on PRs by default', color: 'green', accent: '#34a853' })
d.edge('c0', 'c1', { n: 1 }); d.edge('c1', 'c2')
d.edge('c0', 'c3', { from: 'b', to: 't' }); d.edge('c3', 'c4'); d.edge('c4', 'c5')
d.edge('gh', 'c0', { from: 'r', to: 'l', n: 0, via: [[390, 515], [390, 225]] })

d.node('mn', { x: 1532, y: 170, w: 326, h: 100, icon: 'lucide:git-merge', title: 'Merge to main', sub: 'A human merges. main always runs the full suite', color: 'amber' })
d.node('fl', { x: 1532, y: 300, w: 326, h: 90, icon: 'lucide:timer', title: 'Full run every 2 hours', sub: 'full.yml calls ci.yml on the latest main', color: 'amber' })
d.node('mac', { x: 1532, y: 420, w: 326, h: 80, icon: 'lucide:laptop', title: 'Mac host: share.sh', sub: 'Auto-updates every 5 minutes after a backup', color: 'amber' })
d.node('vps', { x: 1532, y: 520, w: 326, h: 66, icon: 'lucide:server', title: 'VPS: compose rebuild', sub: 'git pull, then rebuild', color: 'amber' })
d.edge('c7', 'mn', { from: 'r', to: 'l', n: 2 })
d.edge('mn', 'fl', { from: 'b', to: 't', kind: 'dashed' }); d.edge('fl', 'mac', { from: 'b', to: 't', kind: 'dashed' })

const T = (id, x, y, icon, title, sub) => d.node(id, { x, y, w: 330, h: 110, icon, title, sub, color: 'violet' })
T('u1', 62, 680, 'brand:pytest', 'Unit and API tests', 'Real Postgres, fake model (FAKE_LLM); never a real API. Per module: make test MODULE=memory')
T('u2', 412, 680, 'lucide:ghost', 'Red team', 'backend/tests/redteam: impostors, name claims, canaries, poisoning, private lobe')
T('u3', 762, 680, 'lucide:activity', 'Evals', 'Offline scenarios, and the long-term-memory gate that runs inside pytest. Live evals are manual and capped')
T('u4', 62, 820, 'lucide:monitor', 'e2e', 'Playwright at desktop and 390 px, with axe accessibility checks')
T('u5', 412, 820, 'lucide:shield', 'Guards', 'Audience gate and RLS pinned by a lock file, OpenAPI snapshot, example-names check, check-env, import cycles ratchet')
T('u6', 762, 820, 'lucide:database', 'Migrations', 'One head, upgrade and downgrade on a scratch database')
d.note(62, 940, 1060, ['Scratch databases only: test, eval, scratch and e2e names are accepted by the safety guard, so a wipe can never hit a live database.'], { color: 'violet' })

d.node('g1', { x: 1202, y: 680, w: 300, h: 100, icon: 'lucide:badge-check', title: 'CI result is green', sub: 'Required status for merge', color: 'rose' })
d.node('g2', { x: 1530, y: 680, w: 330, h: 100, icon: 'lucide:user-check', title: 'Maintainer review', sub: 'CODEOWNERS for protected paths', color: 'rose' })
d.node('g3', { x: 1202, y: 810, w: 300, h: 110, icon: 'lucide:file-text', title: 'Screenshots in the PR', sub: 'Before and after for any visible change', color: 'rose' })
d.node('g4', { x: 1530, y: 810, w: 330, h: 110, icon: 'lucide:flask-conical', title: 'Tests ship with the change', sub: 'Regression test, flag off and on, red-team tests for trust code', color: 'rose' })
d.edge('g1', 'g2', { n: 3 })
d.legend()
export default d
