# PROJECT.md - Bevy (Rust) Game 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "game"
```

---

## 🎮 Bevy 스택 설정

```yaml
game:
  type: "2D"                  # 2D | 3D
  genre: "action"             # action | puzzle | rpg | simulation | casual

  engine: "Bevy"

  bevy:
    version: "0.15"
    features:
      - bevy_ui               # UI 시스템
      - bevy_audio             # 오디오
      - bevy_sprite            # 2D 스프라이트
      # - bevy_pbr             # 3D PBR 렌더링
      # - bevy_gltf            # 3D 모델 로딩
      # - bevy_animation       # 애니메이션

  rust_config:
    edition: "2021"
    profile:
      dev:
        opt_level: 1           # 빠른 컴파일
      release:
        opt_level: 3
        lto: "thin"

  dependencies:
    # - bevy_rapier2d          # 물리 엔진 (2D)
    # - bevy_rapier3d          # 물리 엔진 (3D)
    # - bevy_egui              # 디버그 UI
    # - bevy_asset_loader      # 에셋 로딩
    # - leafwing-input-manager # 입력 관리

  targets:
    - windows
    - macos
    - linux
    # - wasm32 (WebAssembly)

  features:
    multiplayer: false
    save_system: true
    hot_reload: true           # 핫 리로딩 (개발 편의)
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer                # Bevy는 코드 중심 개발
    - devops
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
      clippy: true

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Bevy 요구사항

```yaml
bevy_requirements:
  performance:
    target_fps: 60
    min_fps: 30
    ecs_optimization: true    # ECS 쿼리 최적화

  architecture:
    pattern: "ECS"            # Entity Component System (Bevy 기본)
    plugins: true             # 플러그인 기반 모듈화
    states: true              # Bevy States (게임 상태 관리)
    events: true              # Bevy Events (이벤트 기반 통신)
    resources: true           # 글로벌 리소스

  quality:
    unit_tests: true
    integration_tests: true
    benchmarks: false         # criterion 벤치마크
```

## 환경변수

```yaml
env_vars:
  optional:
    - LOG_LEVEL
    - RUST_LOG
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
  format:
    code: "RustDoc"
```
