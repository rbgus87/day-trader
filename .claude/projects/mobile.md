# PROJECT.md - Mobile 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "mobile"
```

---

## 📱 Mobile 스택 설정

```yaml
mobile:
  targets:
    - ios
    - android

  framework: "React Native"   # React Native | Flutter | Native

  # React Native 설정
  react_native:
    expo: true                # Expo managed workflow
    router: "Expo Router"     # Expo Router | React Navigation
    state: "Zustand"          # Zustand | Redux | Jotai
    ui: "React Native Paper"  # Paper | NativeBase | Tamagui | gluestack

  # Flutter 설정 (framework: "Flutter" 시 사용)
  # flutter:
  #   state: "Riverpod"       # Riverpod | Bloc | GetX | Provider
  #   router: "go_router"     # go_router | auto_route

  # Native iOS 설정 (framework: "Native" 시 사용)
  # ios_native:
  #   ui: "SwiftUI"           # SwiftUI | UIKit
  #   architecture: "MVVM"    # MVVM | TCA | VIPER

  # Native Android 설정 (framework: "Native" 시 사용)
  # android_native:
  #   ui: "Compose"           # Compose | XML Views
  #   architecture: "MVVM"    # MVVM | MVI | Clean

  backend:
    type: "BaaS"
    service: "Supabase"       # Supabase | Firebase | AWS Amplify
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles: []
  # disabled_roles:
  #   - devops      # EAS 사용 시
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

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Mobile 요구사항

```yaml
mobile_requirements:
  auth:
    providers: ["email"]      # email | google | github | apple | kakao
    biometric: false          # Face ID / Fingerprint
    session: "jwt"

  i18n:
    enabled: false
    default_locale: "ko"
    locales: ["ko", "en"]

  performance:
    startup_time: 2000        # ms
    memory_limit: 150         # MB

  platforms:
    ios:
      min_version: "14.0"
      target_devices: ["iphone", "ipad"]
    android:
      min_sdk: 24             # Android 7.0
      target_sdk: 34

  features:
    push_notifications: true
    deep_linking: true
    offline_mode: false
    background_sync: false
```

## 환경변수

```yaml
env_vars:
  required:
    - SUPABASE_URL
    - SUPABASE_KEY
  optional:
    - SENTRY_DSN
    - ANALYTICS_ID
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    api_docs: true
    changelog: true
```
