# 토큰 효율화 전략

> **출처**: TEAM-DETAILED.md > token-strategy 모듈

## 동적 스킬 로딩

```yaml
skill_loading:
  # Phase별 로드 규칙
  phase_1:
    always_load: [orchestrator]
    load_compact: true  # 압축 버전 사용

  phase_2:
    always_load: [bootstrapper]
    optional: [security]  # 의존성 감사 필요 시
    load_compact: true

  phase_3:
    load_on_demand: true  # 활성화된 역할만 로드
    also_load: [qa]  # TDD 가이드 제공
    expand_on_error: true  # 에러 시 전체 SKILL 로드

  phase_4:
    always_load: [qa, security]
    load_compact: true

  # 컴팩트 버전 위치
  compact_skills: "skills/team/_compact/"
```

## 상세 모듈 로딩

```yaml
detailed_modules:
  location: "detailed/"
  index: "TEAM-DETAILED.md"

  # 모듈별 로드 규칙 (온디맨드)
  modules:
    workflow:
      file: "detailed/workflow.md"
      load_when:
        - Phase 전환 시
        - 작업 흐름 참조 필요 시
      tokens: ~650

    collaboration:
      file: "detailed/collaboration.md"
      load_when:
        - 핸드오프 체크리스트 필요 시
        - API 계약 프로토콜 참조 시
        - Phase 전환 프롬프트 필요 시
      tokens: ~1,700

    error-recovery:
      file: "detailed/error-recovery.md"
      load_when:
        - 에러 발생 시만
        - 복구 패턴 참조 필요 시
      tokens: ~1,000

    role-boundaries:
      file: "detailed/role-boundaries.md"
      load_when:
        - --with/--without 사용 시
        - 역할 비활성화 판단 시
      tokens: ~750

    token-strategy:
      file: "detailed/token-strategy.md"
      load_when:
        - 컨텍스트 관리 결정 시
        - 토큰 예산 확인 시
      tokens: ~1,100

  # 기본 로드: 인덱스만 (~350 토큰)
  # 필요 시 개별 모듈 온디맨드 로드
```

## 플러그인 가이드 로딩

```yaml
plugin_guides:
  default: "PLUGINS-QUICK.md"    # 기본 로드 (~2,000 토큰)
  full: "PLUGINS.md"             # 필요 시 확장 (~5,350 토큰)
  expand_when:
    - 플러그인 상세 설정 필요 시
    - 충돌 감지 규칙 참조 시
    - 권한 JSON 블록 필요 시
```

## 프루닝 규칙

```yaml
context_pruning:
  # 완료된 Phase 컨텍스트 축소
  after_phase_complete:
    keep: "결과 요약 (10줄 이내)"
    remove: "상세 분석 과정"

  # 유휴 역할 언로드
  idle_timeout: "3 Phase 동안 미사용 시"

  # 에러 없으면 제외
  exclude_if_success:
    - "트러블슈팅 섹션"
    - "에러 복구 패턴"
```

## 프레임워크 가이드 로딩

```yaml
framework_guides:
  location: "frameworks/"
  compact_location: "frameworks/_compact/"
  loading_rule:
    # PROJECT.md의 frontend.framework 값에 따라 자동 로딩
    trigger: "PROJECT.md의 frontend.framework 값"
    default: compact  # 기본적으로 압축 버전 로드
    expand_when:
      - 프레임워크 특화 에러 발생 시
      - 상세 패턴/코드 예제 필요 시
      - 사용자 명시적 요청 시
  available:
    nuxt:
      trigger_value: "Nuxt.js"
      compact: "frameworks/_compact/nuxt.md"
      full: "frameworks/nuxt.md"
    nextjs:
      trigger_value: "Next.js"
      compact: "frameworks/_compact/nextjs.md"
      full: "frameworks/nextjs.md"
    sveltekit:
      trigger_value: "SvelteKit"
      compact: "frameworks/_compact/sveltekit.md"
      full: "frameworks/sveltekit.md"
    remix:
      trigger_value: "Remix"
      compact: "frameworks/_compact/remix.md"
      full: "frameworks/remix.md"
    astro:
      trigger_value: "Astro"
      compact: "frameworks/_compact/astro.md"
      full: "frameworks/astro.md"
    react-native:
      trigger_value: "React Native"
      compact: "frameworks/_compact/react-native.md"
      full: "frameworks/react-native.md"
    flutter:
      trigger_value: "Flutter"
      compact: "frameworks/_compact/flutter.md"
      full: "frameworks/flutter.md"
    express:
      trigger_value: "Express"
      compact: "frameworks/_compact/express.md"
      full: "frameworks/express.md"
    django:
      trigger_value: "Django"
      compact: "frameworks/_compact/django.md"
      full: "frameworks/django.md"
    nestjs:
      trigger_value: "NestJS"
      compact: "frameworks/_compact/nestjs.md"
      full: "frameworks/nestjs.md"
    angular:
      trigger_value: "Angular"
      compact: "frameworks/_compact/angular.md"
      full: "frameworks/angular.md"
    vue:
      trigger_value: "Vue"
      compact: "frameworks/_compact/vue.md"
      full: "frameworks/vue.md"
```

## Phase별 토큰 예산 가이드

| Phase | 예상 토큰 | 절약 팁 |
|-------|----------|---------|
| **Phase 1** | ~5K | compact 스킬만 로드, brainstorming 필요 시에만 확장 |
| **Phase 2** | ~8K | context7 호출 3회 이내, 프레임워크 compact 가이드 |
| **Phase 3** | ~15-30K | 활성 역할만 로드, 완료된 역할 즉시 언로드 |
| **Phase 4** | ~10K | QA compact + playwright 결과 요약 |
| **Phase 5** | ~5K/iteration | ralph-loop 반복당, max-iterations로 상한 |

**절약 전략:**
- 단순 작업: compact 스킬만 사용 → 전체 대비 ~70% 절약
- 에러 미발생: 트러블슈팅 섹션 제외 → ~10% 절약
- 역할 제한 (`--with`): 불필요 역할 미로드 → 역할당 ~3K 절약
- 모듈화: TEAM-DETAILED 인덱스만 로드 → 82% 절약 (필요 모듈만 온디맨드)
- 플러그인: PLUGINS-QUICK 기본 로드 → 63% 절약 (상세 필요 시만 확장)

> **인덱스**: `TEAM-DETAILED.md` | **빠른 참조**: `TEAM-QUICK.md`
