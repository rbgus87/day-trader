# TEAM-QUICK.md - Veteran Development Team Quick Reference

AI development team for solo developers. All members are veteran experts with 30+ years of experience.

## Team Structure

```
═════════════════════════════════════════════════════════════════════════════════
                              ORCHESTRATOR
                    (Analysis, Tech Stack, Coordination)
                                     │
                                     ▼
                          ┌─────────────────┐
                          │   BOOTSTRAPPER  │
                          │ (Project Setup) │
                          └────────┬────────┘
                                   │
┌─────────┬─────────┬─────────┬────┴────┬─────────┬─────────┬─────────┬─────────┐
▼         ▼         ▼         ▼         ▼         ▼         ▼         ▼         ▼
Designer  Frontend  Backend   Perf      A11y    Security   DevOps      QA
          Architect Architect Architect Architect Engineer  Engineer  Engineer
═════════════════════════════════════════════════════════════════════════════════
```

## Role Summary

| Role | Skill Path | Core Responsibility |
|------|-----------|---------------------|
| Orchestrator | `skills/team/orchestrator/` | Request analysis, tech decisions, task distribution |
| Bootstrapper | `skills/team/bootstrapper/` | Project init, dependencies, environment verification |
| Product Designer | `skills/team/product-designer/` | UX/UI design, wireframes, component specs |
| Frontend Architect | `skills/team/frontend-architect/` | Web/mobile UI, state management, client architecture |
| Backend Architect | `skills/team/backend-architect/` | API design, DB modeling, business logic |
| Performance Architect | `skills/team/performance-architect/` | Optimization, Web Vitals, bundle/query tuning |
| Accessibility Architect | `skills/team/accessibility-architect/` | WCAG compliance, screen reader, keyboard nav |
| Security Engineer | `skills/team/security-engineer/` | Security review, vulnerability assessment, OWASP |
| DevOps Engineer | `skills/team/devops-engineer/` | Infrastructure, CI/CD, deployment, monitoring |
| QA Engineer | `skills/team/qa-engineer/` | TDD guide (Phase 3), E2E testing (Phase 4) |

## Execution Modes

| Mode | Behavior | Best For |
|------|----------|----------|
| `auto` | Runs to completion automatically | Simple tasks, trusted patterns |
| `step` | Requests approval at each Phase | Complex tasks, first use |
| `hybrid` | Confirms only critical decisions | General use (default) |

## Workflow Overview

| Phase | Owner | Key Activity | Key Plugins |
|-------|-------|-------------|-------------|
| **1. Analysis** | Orchestrator | Request analysis, tech stack, task breakdown | brainstorming, writing-plans |
| **2. Setup** | Bootstrapper | Project init, dependencies, verification | context7 |
| **3. Parallel Dev** | Expert Team | UI/API implementation, TDD guide, reviews | feature-dev, context7, test-driven-development |
| **4. Validation** | QA + Security | Full test suite, E2E, regression, security audit | playwright, systematic-debugging |
| **5. Improvement** | All | ralph-loop (--max-iterations 10 required) | role-specific plugins |

## Model Routing (Recommended)

| Task Type | Recommended Model | Reason |
|-----------|------------------|--------|
| Phase 1 analysis, architecture | Opus | Complex reasoning |
| Phase 2 project setup | Sonnet | Standard patterns |
| Phase 3 code implementation | Sonnet | Speed-quality balance |
| Phase 3 security/architecture review | Opus | Deep analysis |
| Phase 4 test execution | Sonnet | Pattern-based |
| Simple fixes, formatting | Haiku | Cost saving |

## Commands

| Command | Description |
|---------|-------------|
| `/team [request]` | Launch full team |
| `/team [request] --with [roles]` | Activate specific experts |
| `/ralph-loop` | Continuous improvement loop (**`--max-iterations 10` required**) |
| `/cancel-ralph` | Manually stop improvement loop |

> **Detailed Guide**: Collaboration protocols, handoff checklists, error recovery, role boundaries, token strategy → `TEAM-DETAILED.md` (index → `detailed/` modules on-demand)
