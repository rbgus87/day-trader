# PROJECT.md - CLI/시스템 도구 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "cli"
```

---

## ⚡ CLI 스택 설정

```yaml
cli:
  type: "tool"                # tool | daemon | library | system

  language: "Rust"            # Rust | Go | Python | Node.js

  # Rust CLI 설정
  rust:
    framework: "clap"         # clap | argh | structopt
    async: "tokio"            # tokio | async-std | smol
    error: "anyhow"           # anyhow | thiserror | eyre
    logging: "tracing"        # tracing | log | env_logger

  # Go CLI 설정 (language: "Go" 시 사용)
  # go:
  #   framework: "cobra"      # cobra | urfave/cli | kong
  #   config: "viper"         # viper | koanf
  #   logging: "zerolog"      # zerolog | zap | slog

  # Python CLI 설정 (language: "Python" 시 사용)
  # python:
  #   framework: "typer"      # typer | click | argparse
  #   packaging: "poetry"     # poetry | pip | uv

  # Node.js CLI 설정 (language: "Node.js" 시 사용)
  # node:
  #   framework: "commander"  # commander | yargs | oclif
  #   runtime: "Bun"          # Node.js | Bun | Deno

  distribution:
    binary: true              # 단일 바이너리 배포
    cross_compile: true       # 크로스 컴파일
    targets:
      - x86_64-unknown-linux-gnu
      - x86_64-apple-darwin
      - aarch64-apple-darwin
      - x86_64-pc-windows-msvc
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer                # CLI는 UI 디자인 불필요
  # disabled_roles:
  #   - frontend              # 필요 시 활성화
  auto_security_review: true
  default_mode: "hybrid"
```

## 프로젝트 컨벤션

```yaml
conventions:
  code_style:
    rust:
      edition: "2021"
      formatter: "rustfmt"
    # go:
    #   formatter: "gofmt"
    #   linter: "golangci-lint"
    # python:
    #   formatter: "black"
    #   linter: "ruff"

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## CLI 요구사항

```yaml
cli_requirements:
  platforms:
    - linux-x64
    - linux-arm64
    - macos-x64
    - macos-arm64
    - windows-x64

  performance:
    startup_time: 100         # ms
    memory_limit: 50          # MB

  features:
    config_file: true         # 설정 파일 지원
    shell_completion: true    # bash/zsh/fish 자동완성
    man_page: false           # man 페이지 생성
    colored_output: true      # 컬러 출력

  distribution:
    homebrew: false           # Homebrew formula
    cargo: false              # crates.io (Rust)
    npm: false                # npm registry
    apt: false                # apt repository
    winget: false             # Windows Package Manager
    github_release: true      # GitHub Releases
```

## 환경변수

```yaml
env_vars:
  optional:
    - CONFIG_PATH
    - LOG_LEVEL
    - DEBUG
    - NO_COLOR
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    man_page: false
  format:
    code: "RustDoc"           # RustDoc | GoDoc
```
