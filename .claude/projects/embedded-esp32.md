# PROJECT.md - ESP32 Embedded 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "embedded"
```

---

## 🔌 ESP32 스택 설정

```yaml
embedded:
  type: "firmware"            # firmware | rtos | bare-metal

  target:
    mcu: "ESP32"              # ESP32 | ESP32-S3 | ESP32-C3 | ESP32-H2
    architecture: "Xtensa"    # Xtensa (ESP32/S3) | RISC-V (C3/H2)
    flash_size: 4             # MB
    psram: false              # PSRAM 사용 여부

  language: "Rust"            # Rust | C (ESP-IDF)

  # Rust ESP32 설정
  rust_embedded:
    hal: "esp-hal"            # esp-hal (no_std) | esp-idf-hal (std)
    runtime: "embassy"        # embassy | esp-idf (std) | bare-metal
    std: false                # no_std (베어메탈) | std (ESP-IDF 기반)
    allocator: false

  # C ESP-IDF 설정 (language: "C" 시 사용)
  # esp_idf:
  #   version: "5.x"
  #   build: "CMake"
  #   component_manager: true
  #   rtos: "FreeRTOS"        # ESP-IDF 기본 내장

  connectivity:
    wifi: true
    bluetooth: false          # BLE | Classic | Both | false
    esp_now: false            # ESP-NOW P2P 통신

  protocols:
    - MQTT
    # - HTTP/REST
    # - WebSocket
    # - mDNS

  peripherals:
    - GPIO
    - I2C
    - SPI
    - UART
    - ADC
    # - PWM (LEDC)
    # - RMT
    # - Touch Sensor
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer
    - frontend                # 컴패니언 앱 필요 시 활성화
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

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## ESP32 요구사항

```yaml
esp32_requirements:
  constraints:
    flash_size: 4             # MB
    ram_size: 520             # KB (내부 SRAM)
    clock_speed: 240          # MHz

  power:
    battery_powered: false
    deep_sleep: true
    light_sleep: false
    target_active: 100        # mA (활성 모드)
    target_sleep: 0.01        # mA (딥슬립)

  reliability:
    watchdog: true
    ota_update: true          # 무선 펌웨어 업데이트
    secure_boot: false
    flash_encryption: false
    nvs_encryption: false     # Non-Volatile Storage 암호화

  connectivity:
    wifi_reconnect: true      # 자동 재연결
    mqtt_qos: 1               # 0 | 1 | 2
    keepalive: 60             # 초
```

## 환경변수

```yaml
env_vars:
  required:
    - WIFI_SSID
    - WIFI_PASSWORD
  optional:
    - MQTT_BROKER
    - MQTT_PORT
    - MQTT_USER
    - MQTT_PASSWORD
    - OTA_SERVER
    - LOG_LEVEL
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    pinout_diagram: false
  format:
    code: "RustDoc"
```
