# PROJECT.md - Godot Game 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "game"
```

---

## 🎮 Godot 스택 설정

```yaml
game:
  type: "2D"                  # 2D | 3D
  genre: "platformer"         # action | puzzle | rpg | simulation | casual | platformer

  engine: "Godot"

  godot:
    version: "4.x"
    language: "GDScript"      # GDScript | C# | GDExtension (C++)
    renderer: "Vulkan"        # Vulkan (Forward+) | Vulkan (Mobile) | Compatibility (OpenGL)
    physics: "Godot Physics"  # Godot Physics | Jolt Physics

  godot_features:
    tilemap: true             # 타일맵 (2D)
    navigation: false         # 경로 탐색
    animation_tree: true      # 애니메이션 트리
    state_machine: true       # 상태 머신
    shaders: false            # 커스텀 셰이더
    particles: false          # 파티클 시스템

  targets:
    - windows
    - macos
    - linux
    # - ios
    # - android
    # - web (HTML5)

  features:
    multiplayer: false
    leaderboard: false
    save_system: true
    localization: false
    accessibility: false
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - devops                  # 게임은 보통 수동 빌드/배포
  auto_security_review: true
  default_mode: "hybrid"
```

## 프로젝트 컨벤션

```yaml
conventions:
  code_style:
    gdscript:
      indent: "tab"
      naming: "snake_case"    # GDScript 표준

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Godot 요구사항

```yaml
godot_requirements:
  performance:
    target_fps: 60
    min_fps: 30
    resolution: "1080p"

  architecture:
    scene_tree: true          # 씬 트리 구조 활용
    autoload: true            # 글로벌 싱글톤 (GameManager 등)
    signals: true             # 시그널 기반 통신
    resources: true           # 커스텀 리소스

  quality:
    unit_tests: false         # GUT (Godot Unit Test)
    profiling: true           # Godot Debugger/Profiler
```

## 환경변수

```yaml
env_vars:
  optional:
    - LOG_LEVEL
    - BACKEND_URL
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    gdd: false
```
