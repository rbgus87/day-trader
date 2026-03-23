# 성능 측정 프레임워크

팀 실행 성과를 측정하고 개선하기 위한 메트릭 가이드.

## 팀 실행 메트릭

### Phase별 완료 지표

| Phase | 측정 항목 | 목표 | 측정 방법 |
|-------|---------|------|----------|
| **Phase 1** | 분석 정확도 | 재분석 0회 | Phase 3에서 스펙 변경 빈도 |
| **Phase 2** | 설정 성공률 | 1회 시도 성공 | 빌드/실행 에러 발생 여부 |
| **Phase 3** | 단위 테스트 통과율 | ≥90% | `npm test` 결과 |
| **Phase 4** | E2E 테스트 통과율 | 100% | Playwright 결과 |
| **Phase 4** | 보안 취약점 | 0건 (High/Critical) | Security 검토 결과 |
| **Phase 5** | 개선 효과 | 측정 가능한 개선 | ralph-loop 반복당 개선률 |

### 코드 품질 메트릭

```yaml
code_quality:
  # 자동 측정 가능
  measurable:
    test_coverage: "≥80% (목표)"
    lint_errors: "0 (필수)"
    type_errors: "0 (필수)"
    bundle_size: "프레임워크 기본 대비 +20% 이내"

  # 수동 확인 필요
  manual_review:
    code_duplication: "DRY 원칙 준수"
    naming_consistency: "프로젝트 컨벤션 일관성"
    error_handling: "모든 에러 경로 처리"
```

## 웹 성능 메트릭 (Web Vitals)

Performance Architect가 측정하는 핵심 지표:

| 메트릭 | 설명 | 목표 (Good) | 측정 도구 |
|--------|------|------------|----------|
| **LCP** | Largest Contentful Paint | ≤ 2.5s | Lighthouse, Playwright |
| **INP** | Interaction to Next Paint | ≤ 200ms | Lighthouse |
| **CLS** | Cumulative Layout Shift | ≤ 0.1 | Lighthouse |
| **FCP** | First Contentful Paint | ≤ 1.8s | Lighthouse |
| **TTFB** | Time to First Byte | ≤ 800ms | 서버 로그 |
| **Bundle Size** | JS 번들 크기 | ≤ 200KB (gzip) | Webpack Analyzer |

### Lighthouse 자동화

```bash
# Playwright로 Lighthouse 실행 (Phase 4)
# @playwright MCP 활용
npx playwright test --project=lighthouse
```

## 접근성 메트릭

Accessibility Architect가 측정하는 지표:

| 메트릭 | 기준 | 측정 방법 |
|--------|------|----------|
| WCAG 2.1 AA 준수 | 모든 항목 통과 | axe-core 자동 스캔 |
| 키보드 내비게이션 | 모든 인터랙티브 요소 접근 가능 | Playwright a11y snapshot |
| 스크린 리더 호환 | 의미 있는 텍스트 제공 | ARIA 레이블 검사 |
| 색상 대비 | 4.5:1 이상 (일반 텍스트) | 자동 대비 검사 |

## 토큰 사용 메트릭

비용 효율성 측정:

| 항목 | 측정 | 최적화 방법 |
|------|------|------------|
| Phase별 토큰 사용량 | 대화 길이 모니터링 | compact 스킬 사용 |
| 에이전트당 토큰 | 서브에이전트 사용 시 | 필요한 경우만 병렬화 |
| ralph-loop 반복당 토큰 | 반복 횟수 × 평균 토큰 | `--max-iterations` 제한 |
| 프레임워크 가이드 로딩 | compact vs full | compact 우선, 필요 시 확장 |

### 토큰 절약 체크리스트

- [ ] Phase 완료 시 컨텍스트 프루닝 적용
- [ ] 유휴 역할 언로드 (3 Phase 미사용 시)
- [ ] compact 스킬 우선 로딩
- [ ] 에러 없으면 트러블슈팅 섹션 제외
- [ ] ralph-loop `--max-iterations` 설정
