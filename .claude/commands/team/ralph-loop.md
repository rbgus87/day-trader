# /ralph-loop - 지속적 개선 루프

Phase 4 완료 후 프로젝트를 반복적으로 개선하는 루프에 진입합니다.

## 사용법

```bash
/ralph-loop "개선 요청"                                    # 기본 (10회 반복)
/ralph-loop "UI 개선해줘" --max-iterations 10              # 명시적 반복 제한
/ralph-loop "성능 최적화" --max-iterations 20 --completion-promise 'LCP < 2.5s'
```

> **필수**: `--max-iterations`를 반드시 포함하세요. 미설정 시 무제한 반복으로 비용 폭증 위험.

## 안전 설정

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--max-iterations` | **10** (필수 권장) | 최대 반복 횟수 (최대 권장: 30) |
| `--completion-promise` | 없음 (선택) | 조건 충족 시 자동 종료 텍스트 |

## 실행 흐름

```
/ralph-loop 진입
    │
    ├── 1. 개선 요청 분석
    │   └── Orchestrator가 적절한 역할 배정
    │
    ├── 2. 역할별 개선 실행
    │   ├── "UI가 밋밋해" → Designer + Frontend
    │   │   └── @frontend-design, @playwright
    │   ├── "로딩이 느려" → Performance Architect
    │   │   └── @playwright (Lighthouse), @context7
    │   ├── "키보드로 조작 안돼" → Accessibility Architect
    │   │   └── @playwright (a11y snapshot)
    │   ├── "보안 취약점 없어?" → Security Engineer
    │   │   └── @feature-dev:code-reviewer
    │   ├── "테스트 커버리지 높여줘" → QA Engineer
    │   │   └── @superpowers:test-driven-development
    │   ├── "기능 추가해줘" → Frontend/Backend
    │   │   └── @feature-dev:feature-dev
    │   └── "코드 구조 개선해줘" → Orchestrator
    │       └── @feature-dev:code-architect
    │
    ├── 3. 반복 (--max-iterations 범위 내)
    │   └── 각 반복마다 개선 사항 보고
    │
    └── 4. 종료
        ├── max-iterations 도달 → 자동 종료
        ├── completion-promise 충족 → 자동 종료
        └── /cancel-ralph → 수동 조기 종료
```

## 전제 조건

- Phase 4 (검증 및 완료)가 완료된 상태
- 프로젝트가 빌드 가능하고 기본 테스트 통과
- `--max-iterations` 없이 실행 **절대 금지**

## 반복당 출력 형식

```markdown
## 🔄 반복 #N / max-iterations

### 개선 사항
- ✅ [완료된 개선 내용]
- 🔄 [진행 중인 개선]

### 다음 반복 계획
- [ ] [다음 개선 항목]

### 종료 조건 확인
- iterations: N / max-iterations
- completion-promise: [미충족 / 충족]
```

## 주의사항

1. **비용 관리**: 반복마다 토큰을 소비합니다. 10회 기본값 권장.
2. **범위 제한**: 한 번에 하나의 개선 주제에 집중하세요.
3. **결과 확인**: 매 반복 후 결과를 검토하고 필요 시 `/cancel-ralph`로 종료.
4. **병렬 개선**: "여러 개선 동시 진행해줘" → `@superpowers:subagent-driven-development` 활용

---

## 참조

- 팀 전체 설정: `TEAM-QUICK.md`
- 플러그인 가이드: `PLUGINS.md`
- 수동 조기 종료: `/cancel-ralph`
