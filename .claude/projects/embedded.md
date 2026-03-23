# PROJECT.md - Embedded/IoT 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "embedded"
```

---

## 🔌 Embedded 스택 설정

```yaml
embedded:
  type: "firmware"            # firmware | rtos | bare-metal | linux-based

  target:
    mcu: "ESP32"              # ESP32 | STM32 | nRF52 | RP2040 | AVR
    architecture: "Xtensa"    # Xtensa | ARM Cortex-M | RISC-V | AVR

  language: "Rust"            # Rust | C | C++ | MicroPython | Arduino

  # Rust Embedded 설정
  rust_embedded:
    hal: "esp-hal"            # esp-hal | stm32-hal | nrf-hal | rp-hal
    async: "embassy"          # embassy | rtic | bare-metal
    allocator: false          # 동적 할당 사용 여부

  # C/C++ Embedded 설정 (language: "C" 또는 "C++" 시 사용)
  # c_embedded:
  #   framework: "ESP-IDF"    # ESP-IDF | STM32CubeMX | Zephyr | FreeRTOS
  #   build: "CMake"          # CMake | PlatformIO | Make
  #   rtos: "FreeRTOS"        # FreeRTOS | Zephyr | ThreadX | none

  # Arduino/MicroPython 설정
  # arduino:
  #   platform: "PlatformIO"  # Arduino IDE | PlatformIO

  connectivity:
    wifi: true
    bluetooth: false
    lora: false
    cellular: false

  protocols:
    - MQTT
    # - CoAP
    # - Modbus
    # - CAN

  peripherals:
    - GPIO
    - I2C
    - SPI
    - UART
    # - ADC
    # - PWM
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer                # 임베디드는 UI 디자인 불필요
    - frontend                # 프론트엔드 불필요 (컴패니언 앱 제외)
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
    # cpp:
    #   standard: "C++17"
    #   formatter: "clang-format"

  commit:
    format: "conventional"

  branching:
    strategy: "github-flow"
    main: "main"
    feature: "feature/*"
```

## Embedded 요구사항

```yaml
embedded_requirements:
  # 하드웨어 제약
  constraints:
    flash_size: 4             # MB
    ram_size: 520             # KB
    clock_speed: 240          # MHz

  # 전력
  power:
    battery_powered: false
    deep_sleep: true
    target_consumption: 100   # mA (active)

  # 안정성
  reliability:
    watchdog: true
    ota_update: true
    secure_boot: false
    encryption: false

  # 인증 (필요 시)
  certifications: []
  # certifications:
  #   - CE
  #   - FCC
  #   - KC
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
    code: "RustDoc"           # RustDoc | Doxygen
```
