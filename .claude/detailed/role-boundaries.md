# 역할 경계 매트릭스

> **출처**: TEAM-DETAILED.md > role-boundaries 모듈

## 책임 분담 명확화

| 영역 | 주 담당 | 보조 담당 | 경계선 |
|------|--------|----------|--------|
| **컴포넌트 설계** | Designer | Frontend | Designer가 구조/상태 정의 → Frontend가 구현 |
| **컴포넌트 구현** | Frontend | Designer | Frontend가 코드 작성, Designer가 검토 |
| **DB 스키마** | Backend | Performance | Backend가 설계 → Performance가 인덱스 최적화 |
| **쿼리 최적화** | Performance | Backend | Performance가 분석/권고 → Backend가 적용 |
| **접근성 요구사항** | Designer | Accessibility | Designer가 명세 시 접근성 포함 → A11y가 검증 |
| **접근성 구현** | Frontend | Accessibility | Frontend가 구현 → A11y가 WCAG 감사 |
| **API 보안** | Security | Backend | Security가 검토/권고 → Backend가 구현 |
| **인프라 보안** | Security | DevOps | Security가 정책 정의 → DevOps가 적용 |

## 역할 비활성화 가이드

```yaml
role_deactivation:
  # 안전하게 비활성화 가능
  safe_to_disable:
    devops:
      when: "로컬 개발만, CI/CD 불필요, BaaS 사용"
      impact: "배포 자동화 미설정, 수동 배포 필요"

    accessibility:
      when: "내부 도구, MVP 단계, 접근성 요구 없음"
      impact: "WCAG 미준수, 공공/대외 서비스 시 법적 이슈 가능"
      warning: "프로덕션 전 반드시 활성화 권장"

    performance:
      when: "MVP, 프로토타입, 트래픽 낮음"
      impact: "성능 최적화 미수행"
      warning: "출시 전 활성화 권장"

    designer:
      when: "기존 디자인 시스템 사용, 개발자가 UI 담당"
      impact: "UX 일관성 저하 가능"

  # 비활성화 비권장
  not_recommended:
    qa:
      reason: "테스트 없이 품질 보장 불가"
      alternative: "--mode auto 사용 시에도 최소 테스트"

    frontend:
      reason: "UI 없는 프로젝트 아니면 필수"
      alternative: "CLI 전용 → 비활성화 가능"

    backend:
      reason: "서버 없는 프로젝트 아니면 필수"
      alternative: "정적 사이트 → 비활성화 가능"

  # 절대 비활성화 금지
  never_disable:
    orchestrator:
      reason: "시스템 핵심, 조율자 역할 필수"

    security:
      reason: "보안 검토 없이 배포 위험"
      exception: "명시적 --without security 사용 시 경고 출력"

# 역할 조합 프리셋
presets:
  minimal_web:
    roles: [orchestrator, bootstrapper, frontend, backend]
    use_case: "빠른 웹 프로토타입"
    command: "/team ... --with orchestrator,bootstrapper,frontend,backend"

  full_web:
    roles: [all]
    use_case: "프로덕션 웹 애플리케이션"
    command: "/team ..."

  api_only:
    roles: [orchestrator, bootstrapper, backend, security, devops, qa]
    use_case: "백엔드 API 전용"
    command: "/team ... --without designer,frontend,performance,accessibility"

  cli_tool:
    roles: [orchestrator, bootstrapper, backend, security, qa]
    use_case: "CLI 도구 개발"
    command: "/team ... --without designer,frontend,devops,performance,accessibility"
```

> **인덱스**: `TEAM-DETAILED.md` | **빠른 참조**: `TEAM-QUICK.md`
