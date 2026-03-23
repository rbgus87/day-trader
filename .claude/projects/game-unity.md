# PROJECT.md - Unity Game 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "game"
```

---

## 🎮 Unity 스택 설정

```yaml
game:
  type: "3D"                  # 2D | 3D | VR | AR
  genre: "action"             # action | puzzle | rpg | simulation | casual | platformer

  engine: "Unity"

  unity:
    version: "2022.3 LTS"    # LTS 버전 권장
    render_pipeline: "URP"    # URP | HDRP | Built-in
    scripting: "C#"
    input: "New Input System" # New Input System | Legacy
    physics: "PhysX"          # PhysX | Box2D (2D)
    ui: "UI Toolkit"          # UI Toolkit | uGUI (Canvas)
    networking: "none"        # Netcode for GameObjects | Mirror | Photon | none

  unity_packages:
    addressables: false       # 에셋 번들 관리
    cinemachine: true         # 카메라 제어
    animation_rigging: false  # 애니메이션 리깅
    visual_scripting: false   # 비주얼 스크립팅
    probuilder: false         # 프로토타이핑 3D 도구

  targets:
    - windows
    - macos
    # - linux
    # - ios
    # - android
    # - webgl
    # - switch
    # - playstation
    # - xbox

  features:
    multiplayer: false
    leaderboard: false
    achievements: false
    analytics: false
    ads: false
    iap: false
    save_system: true
    localization: false
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles: []
  auto_security_review: true
  default_mode: "hybrid"
```

## 프로젝트 컨벤션

```yaml
conventions:
  code_style:
    csharp:
      version: "12"
      formatter: "dotnet-format"
      naming: "Unity"         # PascalCase for public, _camelCase for private

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Unity 요구사항

```yaml
unity_requirements:
  performance:
    target_fps: 60
    min_fps: 30
    resolution: "1080p"
    draw_calls_budget: 200
    triangle_budget: 500000

  architecture:
    pattern: "MVC"            # MVC | ECS | ScriptableObject
    dependency_injection: false  # Zenject/VContainer
    event_system: "UnityEvents"  # UnityEvents | C# Events | ScriptableObject

  quality:
    unit_tests: true          # Unity Test Framework
    play_mode_tests: true
    profiling: true           # Unity Profiler
```

## 환경변수

```yaml
env_vars:
  optional:
    - ANALYTICS_KEY
    - BACKEND_URL
    - LOG_LEVEL
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    gdd: false                # Game Design Document
```
