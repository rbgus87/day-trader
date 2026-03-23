# Internationalization (i18n)

Team-Init 템플릿의 다국어 지원 구조입니다.

## 구조

```
i18n/
├── README.md          # 이 파일
└── en/                # English
    ├── CLAUDE.md      # Main entry point
    └── TEAM-QUICK.md  # Team quick reference
```

## 사용법

영문 버전을 사용하려면 해당 파일을 프로젝트 `.claude/` 루트에 복사합니다:

```bash
# 영문 버전으로 교체
cp .claude/i18n/en/CLAUDE.md .claude/CLAUDE.md
cp .claude/i18n/en/TEAM-QUICK.md .claude/TEAM-QUICK.md
```

## 번역 범위

| 파일 | 한국어 (기본) | English |
|------|:------------:|:-------:|
| CLAUDE.md | ✅ | ✅ |
| TEAM-QUICK.md | ✅ | ✅ |
| TEAM-DETAILED.md | ✅ (인덱스 + `detailed/` 모듈 5개) | — |
| PLUGINS-QUICK.md | ✅ | — |
| PLUGINS.md | ✅ | — |
| SKILL.md (10개) | ✅ | — |
| frameworks/ (12종) | ✅ | — |

> TEAM-DETAILED.md(인덱스 + detailed/ 모듈), PLUGINS-QUICK.md, PLUGINS.md, SKILL 파일은
> 기술 문서 특성상 한국어/영어 혼용으로 작성되어 영어 사용자도 충분히 활용 가능합니다.
> 필요 시 추가 번역을 진행합니다.

## 새 언어 추가

1. `i18n/[lang]/` 디렉토리 생성 (예: `i18n/ja/`)
2. 최소 `CLAUDE.md` + `TEAM-QUICK.md` 번역
3. 이 README에 언어 등록
