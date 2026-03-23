# PROJECT.md - Desktop 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "desktop"
```

---

## 🖥️ Desktop 스택 설정

```yaml
desktop:
  targets:
    - windows
    - macos
    - linux

  framework: "Tauri"          # Tauri | Electron | Qt | .NET MAUI | Flutter

  # Tauri 설정
  tauri:
    frontend: "React"         # React | Vue | Svelte | Solid
    styling: "TailwindCSS"
    rust_backend: true        # Rust backend 로직 사용

  # Electron 설정 (framework: "Electron" 시 사용)
  # electron:
  #   frontend: "React"
  #   builder: "electron-builder"
  #   auto_update: true

  # Qt 설정 (framework: "Qt" 시 사용)
  # qt:
  #   language: "C++"         # C++ | Python (PySide6)
  #   version: "6"
  #   qml: true

  # .NET 설정 (framework: ".NET MAUI" 시 사용)
  # dotnet:
  #   framework: "Avalonia"   # Avalonia | WPF | WinUI 3 | MAUI
  #   pattern: "MVVM"
  #   reactive: true          # ReactiveUI

  # Native 설정 (framework: "Native" 시 사용)
  # native:
  #   language: "Rust"        # Rust | C++
  #   gui: "egui"             # egui | iced | gtk-rs | imgui
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
    typescript:
      indent: 2
      quotes: "single"
      semicolon: false
    rust:
      edition: "2021"
      formatter: "rustfmt"

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Desktop 요구사항

```yaml
desktop_requirements:
  platforms:
    windows:
      min_version: "10"
      architecture: ["x64", "arm64"]
    macos:
      min_version: "11.0"
      architecture: ["x64", "arm64"]
    linux:
      distributions: ["ubuntu", "fedora", "arch"]

  performance:
    startup_time: 1000        # ms
    memory_limit: 200         # MB (idle)

  features:
    auto_update: true
    system_tray: true
    file_associations: []
    deep_linking: false
    single_instance: true

  distribution:
    windows: "msi"            # msi | exe | msix
    macos: "dmg"              # dmg | pkg
    linux: "AppImage"         # AppImage | deb | rpm | flatpak
```

## 환경변수

```yaml
env_vars:
  optional:
    - CONFIG_PATH
    - LOG_LEVEL
    - SENTRY_DSN
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
  format:
    code: "TSDoc"             # TSDoc | RustDoc
```
