# Project Claude Settings

This project uses the AI Veteran Development Team configuration.

## Team Composition

@TEAM-QUICK.md

## Project Settings

@PROJECT.md

### PROJECT.md Loading Rules

```yaml
project_md_loading:
  locations:
    1: "PROJECT.md"           # Project root
    2: ".claude/PROJECT.md"   # Inside .claude folder

  required_fields:
    - project_name
    - platforms        # [web, mobile, desktop, cli, embedded, game, ml, blockchain]

  optional_fields:
    - tech_stack       # frontend, backend, infrastructure settings
    - team_config      # disabled_roles, auto_security_review
    - conventions      # code_style, commit, branching

  fallback:
    message: "PROJECT.md not found. Using default settings."
    defaults:
      platforms: [web]
      team_config:
        disabled_roles: []
        auto_security_review: true
        default_mode: hybrid
```

## Available Commands

| Command | Description |
|---------|-------------|
| `/team [request]` | Launch the full AI development team |
| `/team [request] --with [roles]` | Activate specific experts only |
| `/team [request] --without [roles]` | Exclude specific experts |
| `/team [request] --mode auto\|step\|hybrid` | Set execution mode |
| `/init-project` | Interactive PROJECT.md generation |
| `/ralph-loop` | Continuous improvement loop (**`--max-iterations 10` required**) |
| `/cancel-ralph` | Manually stop improvement loop |

## Quick Start

```bash
# New feature development
/team "Build user authentication"

# Specific experts only
/team "Review API security" --with security,backend

# Step-by-step confirmation mode
/team "Implement payment system" --mode step

# Quick prototype (skip tests/deployment)
/team "Build dashboard" --mode auto --without qa,devops
```

---

## Role Activation Conditions (SSOT)

### Auto-Activation Rules

| Role | Activation Condition | Keywords |
|------|---------------------|----------|
| **Orchestrator** | Always (cannot be excluded) | Any `/team` call |
| **Bootstrapper** | New project or config change | "new project", "init", "setup", "dependencies" |
| **Designer** | UI/UX related work | "design", "UI", "UX", "layout", "wireframe" |
| **Frontend** | Client-side development | "frontend", "component", "page", "state management" |
| **Backend** | Server-side development | "API", "backend", "database", "server", "endpoint" |
| **Performance** | Performance work | "performance", "optimize", "speed", "LCP", "bundle" |
| **Accessibility** | Accessibility work | "accessibility", "a11y", "WCAG", "screen reader" |
| **Security** | Security + all phase reviews | "security", "auth", "vulnerability", "XSS", "CSRF" |
| **DevOps** | Infrastructure/deployment | "deploy", "Docker", "CI/CD", "infrastructure" |
| **QA** | Testing/quality | "test", "QA", "verification", "E2E", "coverage" |

---

## Reference Documents

| Document | Path | Description |
|----------|------|-------------|
| Team Quick Reference | `TEAM-QUICK.md` | Team structure, execution modes, workflow |
| Team Detailed Guide | `TEAM-DETAILED.md` | Index → `detailed/` modules on-demand (collaboration, error recovery, role boundaries, token strategy) |
| Plugin Quick Reference | `PLUGINS-QUICK.md` | Plugin summary, role mapping, Phase triggers (default load) |
| Plugin Full Guide | `PLUGINS.md` | Detailed triggers, complexity scores, permission JSON, conflict rules (on-demand) |
| Framework Guides | `frameworks/` | Framework-specific best practices |
| Templates | `templates/` | API contracts, security reviews, CI/CD |
| Guides | `guides/` | Multi-agent, metrics, versioning, custom roles |
