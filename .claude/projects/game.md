# PROJECT.md - Game 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "game"
```

---

## 🎮 Game 스택 설정

```yaml
game:
  type: "3D"                  # 2D | 3D | VR | AR
  genre: "action"             # action | puzzle | rpg | simulation | casual

  engine: "Unity"             # Unity | Godot | Unreal | Bevy | Custom

  # Unity 설정
  unity:
    version: "2022.3 LTS"
    render_pipeline: "URP"    # URP | HDRP | Built-in
    scripting: "C#"
    input: "New Input System"
    networking: "none"        # Netcode | Mirror | Photon | none

  # Godot 설정 (engine: "Godot" 시 사용)
  # godot:
  #   version: "4.x"
  #   language: "GDScript"    # GDScript | C# | GDExtension
  #   renderer: "Vulkan"      # Vulkan | OpenGL | Compatibility

  # Unreal 설정 (engine: "Unreal" 시 사용)
  # unreal:
  #   version: "5.3"
  #   language: "C++"         # C++ | Blueprints | Both

  # Bevy (Rust) 설정 (engine: "Bevy" 시 사용)
  # bevy:
  #   version: "0.13"
  #   features:
  #     - bevy_ui
  #     - bevy_audio
  #     - bevy_pbr

  # Web Game 설정 (engine: "Custom" 시 사용)
  # web_game:
  #   framework: "Phaser"     # Phaser | PixiJS | Three.js | Babylon.js

  targets:
    - windows
    - macos
    # - linux
    # - ios
    # - android
    # - web
    # - switch
    # - playstation
    # - xbox

  features:
    multiplayer: false
    leaderboard: false
    achievements: false
    analytics: false
    ads: false
    iap: false                # In-App Purchase
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles: []
  # disabled_roles:
  #   - backend             # 싱글플레이어 전용 시
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
    # cpp:
    #   standard: "C++20"
    #   formatter: "clang-format"

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Game 요구사항

```yaml
game_requirements:
  # 타겟 성능
  performance:
    target_fps: 60
    min_fps: 30
    resolution: "1080p"       # 720p | 1080p | 4K

  # 플랫폼 요구사항
  platform_requirements:
    pc:
      min_ram: 8              # GB
      min_gpu: "GTX 1060"
    mobile:
      min_ios: "14.0"
      min_android: "21"       # API Level

  # 플레이어 경험
  player:
    save_system: true
    cloud_save: false
    settings_persist: true

  # 수익화
  monetization:
    type: "premium"           # premium | free-to-play | subscription
    ads: false
    iap: false
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
