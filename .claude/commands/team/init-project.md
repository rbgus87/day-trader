# /init-project - PROJECT.md 인터랙티브 생성

프로젝트 도메인과 기술 스택을 선택하여 `PROJECT.md`를 자동 생성합니다.

## 사용법

```bash
/init-project                    # 인터랙티브 모드 (질문-응답)
/init-project web                # 도메인 지정
/init-project game --engine godot  # 도메인 + 옵션
```

## 실행 프로세스

### Step 1: 도메인 선택

사용자에게 프로젝트 도메인을 질문합니다:

| 도메인 | 기반 템플릿 | 세분화 옵션 |
|--------|------------|------------|
| **web** | `projects/web.md` | SSR (`web-ssr.md`), Static (`web-static.md`) |
| **fullstack** | `projects/fullstack.md` | — |
| **mobile** | `projects/mobile.md` | — |
| **desktop** | `projects/desktop.md` | — |
| **cli** | `projects/cli.md` | — |
| **embedded** | `projects/embedded.md` | ESP32 (`embedded-esp32.md`), STM32 (`embedded-stm32.md`) |
| **game** | `projects/game.md` | Unity (`game-unity.md`), Godot (`game-godot.md`), Bevy (`game-bevy.md`) |
| **ml** | `projects/ml.md` | PyTorch (`ml-pytorch.md`), LLM (`ml-llm.md`) |
| **blockchain** | `projects/blockchain.md` | — |
| **monorepo** | `projects/monorepo.md` | — |

### Step 2: 기술 스택 커스터마이징

선택한 템플릿의 기본값을 보여주고, 변경이 필요한 항목을 질문합니다:

```
도메인: web (SSR)
기반 템플릿: projects/web-ssr.md

현재 기본값:
  frontend.framework: Nuxt.js    ← 변경? (Next.js, SvelteKit, Remix)
  frontend.styling: TailwindCSS  ← 변경?
  backend.service: Supabase      ← 변경? (Firebase, Custom)
  infrastructure.hosting: Vercel ← 변경?
```

### Step 3: PROJECT.md 생성

```
생성 위치: PROJECT.md (프로젝트 루트)

내용:
1. 선택한 도메인 템플릿의 구조 복사
2. 사용자 커스텀 값 적용
3. project_name, description 입력 요청
```

### Step 4: 프레임워크 가이드 연동

선택한 프레임워크에 따라 안내:

```
✅ PROJECT.md 생성 완료

🔗 연동된 프레임워크 가이드:
   - frameworks/nuxt.md (전체)
   - frameworks/_compact/nuxt.md (압축)
   → Phase 3에서 자동 로딩됩니다.

💡 다음 단계:
   /team "요청 내용" 으로 개발을 시작하세요.
```

## 출력 형식

```markdown
## ✅ PROJECT.md 생성 완료

### 프로젝트 설정
- **도메인**: {{domain}}
- **프레임워크**: {{framework}}
- **백엔드**: {{backend}}

### 생성된 파일
- `PROJECT.md`

### 다음 단계
1. PROJECT.md 내용 확인 및 필요 시 수정
2. `/team "요청"` 으로 개발 시작
```

## 프레임워크 자동 감지

기존 프로젝트에 `package.json`이 있으면 자동 감지:

```yaml
auto_detect:
  # package.json의 dependencies 키워드로 판별
  nuxt: ["nuxt"]
  next: ["next"]
  sveltekit: ["@sveltejs/kit"]
  remix: ["@remix-run/react"]
  astro: ["astro"]
  react_native: ["react-native"]
  flutter: "pubspec.yaml 존재"

  # 감지 시 동작
  on_detect:
    message: "{{framework}} 프로젝트가 감지되었습니다. 이 설정을 사용할까요?"
    auto_apply: false  # 사용자 확인 후 적용
```

---

## 참조

- 프로젝트 템플릿: `projects/` 폴더
- 프레임워크 가이드: `frameworks/` 폴더
- 팀 가동: `/team`
