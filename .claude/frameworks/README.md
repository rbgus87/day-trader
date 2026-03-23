# Frameworks Reference

프레임워크별 베스트 프랙티스 가이드. 온디맨드 로딩으로 토큰 효율성을 유지합니다.

## 로딩 규칙

```yaml
framework_guides:
  # PROJECT.md의 frontend.framework 값에 따라 자동 로딩
  loading:
    default: compact          # 기본적으로 압축 버전 로드
    expand_when:              # 전체 버전 로드 조건
      - 프레임워크 특화 에러 발생 시
      - 상세 패턴/코드 예제 필요 시
      - 사용자 명시적 요청 시
```

## 사용 가능한 가이드

| 프레임워크 | Compact | Full | 트리거 조건 |
|-----------|---------|------|------------|
| **Nuxt.js** | `_compact/nuxt.md` | `nuxt.md` | `frontend.framework: "Nuxt.js"` |
| **Next.js** | `_compact/nextjs.md` | `nextjs.md` | `frontend.framework: "Next.js"` |
| **SvelteKit** | `_compact/sveltekit.md` | `sveltekit.md` | `frontend.framework: "SvelteKit"` |
| **Remix** | `_compact/remix.md` | `remix.md` | `frontend.framework: "Remix"` |
| **Astro** | `_compact/astro.md` | `astro.md` | `frontend.framework: "Astro"` |
| **React Native** | `_compact/react-native.md` | `react-native.md` | `mobile.framework: "React Native"` |
| **Flutter** | `_compact/flutter.md` | `flutter.md` | `mobile.framework: "Flutter"` |
| **Express.js** | `_compact/express.md` | `express.md` | `backend.framework: "Express"` |
| **Django** | `_compact/django.md` | `django.md` | `backend.framework: "Django"` |
| **NestJS** | `_compact/nestjs.md` | `nestjs.md` | `backend.framework: "NestJS"` |
| **Angular** | `_compact/angular.md` | `angular.md` | `frontend.framework: "Angular"` |
| **Vue** | `_compact/vue.md` | `vue.md` | `frontend.framework: "Vue"` |

## 새 프레임워크 가이드 추가

1. `frameworks/[framework].md` (전체 버전, ~350줄 이내)
2. `frameworks/_compact/[framework].md` (압축 버전, ~60줄 이내)
3. `detailed/token-strategy.md`의 `framework_guides.available`에 등록
4. 관련 스킬 파일에 교차 참조 추가
