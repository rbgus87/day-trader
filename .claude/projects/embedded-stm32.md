# PROJECT.md - STM32 Embedded 프로젝트 설정

## 프로젝트 정보

```yaml
project_name: "My Project"
description: "프로젝트 설명을 입력하세요"
version: "0.1.0"
project_type: "embedded"
```

---

## 🔌 STM32 스택 설정

```yaml
embedded:
  type: "firmware"            # firmware | rtos | bare-metal

  target:
    mcu: "STM32"              # STM32F4 | STM32H7 | STM32L4 | STM32G4 | STM32U5
    series: "STM32F4"         # 시리즈 지정
    architecture: "ARM Cortex-M4"  # Cortex-M0+ | M3 | M4 | M7 | M33

  language: "Rust"            # Rust | C | C++

  # Rust STM32 설정
  rust_embedded:
    hal: "stm32-hal"          # stm32-hal | embassy-stm32
    runtime: "embassy"        # embassy | rtic | cortex-m-rt
    allocator: false
    probe: "probe-rs"         # probe-rs | OpenOCD

  # C/C++ STM32 설정 (language: "C" 시 사용)
  # stm32_c:
  #   framework: "STM32CubeMX"  # STM32CubeMX | STM32CubeIDE | Zephyr
  #   hal: "HAL"              # HAL | LL (Low-Level) | CMSIS
  #   rtos: "FreeRTOS"        # FreeRTOS | Zephyr | ThreadX | none
  #   build: "CMake"          # CMake | Makefile

  connectivity:
    uart: true
    spi: true
    i2c: true
    can: false                # CAN / CAN FD
    usb: false                # USB Device / Host
    ethernet: false

  protocols:
    - UART
    # - Modbus RTU
    # - CAN
    # - USB CDC

  peripherals:
    - GPIO
    - UART
    - SPI
    - I2C
    - TIM (Timer)
    - ADC
    # - DAC
    # - DMA
    # - PWM
```

---

## 팀 설정

```yaml
team_config:
  disabled_roles:
    - designer
    - frontend
    - devops                  # CI/CD는 선택적
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

## STM32 요구사항

```yaml
stm32_requirements:
  constraints:
    flash_size: 512           # KB
    ram_size: 128             # KB
    clock_speed: 168          # MHz

  power:
    battery_powered: false
    low_power_modes: true     # Sleep / Stop / Standby
    target_active: 50         # mA

  reliability:
    watchdog: true            # IWDG / WWDG
    fault_handler: true       # HardFault 핸들러
    bootloader: true          # 커스텀 부트로더

  debug:
    interface: "SWD"          # SWD | JTAG
    probe: "ST-Link"          # ST-Link | J-Link | CMSIS-DAP
    rtt: true                 # Real-Time Transfer (로깅)
```

## 환경변수

```yaml
env_vars:
  optional:
    - DEVICE_ID
    - LOG_LEVEL
    - BAUD_RATE
```

## 문서화 설정

```yaml
documentation:
  language: "ko"
  auto_generate:
    readme: true
    changelog: true
    pinout_diagram: true
  format:
    code: "RustDoc"
```
