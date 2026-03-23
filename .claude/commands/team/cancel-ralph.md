# /cancel-ralph - 개선 루프 수동 종료

`/ralph-loop` 실행 중 수동으로 조기 종료합니다.

## 사용법

```bash
/cancel-ralph                    # 즉시 종료
/cancel-ralph "이유 메모"         # 종료 사유와 함께 종료
```

## 동작

```
/cancel-ralph 실행
    │
    ├── 1. 현재 진행 중인 작업 완료 (중단하지 않음)
    ├── 2. 종료 보고 생성
    │   ├── 완료된 반복 횟수
    │   ├── 수행된 개선 사항 요약
    │   └── 미완료 항목 목록
    └── 3. ralph-loop 종료
```

## 출력 형식

```markdown
## ⏹️ Ralph Loop 종료

### 실행 요약
- **총 반복**: N회 (max-iterations 중)
- **종료 사유**: 수동 종료 (/cancel-ralph)

### 완료된 개선
- ✅ [개선 1]
- ✅ [개선 2]

### 미완료 항목
- ⬜ [남은 개선 사항]

### 다음 단계 권장
- [ ] [후속 작업 제안]
```

## 종료 시점 가이드

| 상황 | 권장 행동 |
|------|----------|
| 충분히 개선됨 | `/cancel-ralph` |
| 방향 전환 필요 | `/cancel-ralph` → 새 `/ralph-loop` |
| 비용 우려 | `/cancel-ralph` |
| 특정 목표 달성 | `--completion-promise` 자동 종료 활용 |

---

## 참조

- 개선 루프 시작: `/ralph-loop`
- 팀 전체 설정: `TEAM-QUICK.md`
