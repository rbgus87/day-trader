# 컴포넌트 명세서 템플릿

## {{COMPONENT_NAME}}

### 개요

| 항목 | 내용 |
|------|------|
| **컴포넌트명** | `{{ComponentName}}` |
| **용도** | {{DESCRIPTION}} |
| **카테고리** | UI / Layout / Form / Feedback / Navigation |
| **복합 여부** | Atomic / Molecular / Organism |

### 와이어프레임

```
┌─────────────────────────────────────────┐
│  [Icon]  {{Title}}                 [X]  │
├─────────────────────────────────────────┤
│                                         │
│  {{Content Area}}                       │
│                                         │
├─────────────────────────────────────────┤
│              [Secondary] [Primary]      │
└─────────────────────────────────────────┘
```

### Props

| Prop | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `title` | `string` | ✅ | - | 컴포넌트 제목 |
| `variant` | `'default' \| 'outlined' \| 'ghost'` | ❌ | `'default'` | 스타일 변형 |
| `size` | `'sm' \| 'md' \| 'lg'` | ❌ | `'md'` | 크기 |
| `disabled` | `boolean` | ❌ | `false` | 비활성화 상태 |
| `loading` | `boolean` | ❌ | `false` | 로딩 상태 |
| `onAction` | `() => void` | ❌ | - | 액션 콜백 |

### Variants

| Variant | 용도 | 시각적 특징 |
|---------|------|-------------|
| `default` | 일반 사용 | 배경색 있음, 그림자 |
| `outlined` | 보조 액션 | 테두리만, 투명 배경 |
| `ghost` | 최소 스타일 | 테두리/배경 없음 |

### States

| 상태 | 조건 | 시각적 변화 |
|------|------|-------------|
| **Default** | 기본 상태 | 표준 스타일 적용 |
| **Hover** | 마우스 오버 | 배경색 어둡게 (5%) |
| **Active** | 클릭 중 | 배경색 더 어둡게 (10%), scale(0.98) |
| **Focus** | 키보드 포커스 | 포커스 링 표시 (2px solid primary) |
| **Disabled** | `disabled=true` | 투명도 50%, 커서 not-allowed |
| **Loading** | `loading=true` | 콘텐츠 투명도 0, 스피너 표시 |

### Sizes

| Size | Height | Font Size | Padding | Icon Size |
|------|--------|-----------|---------|-----------|
| `sm` | 32px | 14px | 8px 12px | 16px |
| `md` | 40px | 16px | 12px 16px | 20px |
| `lg` | 48px | 18px | 16px 24px | 24px |

### 반응형 동작

| 브레이크포인트 | 동작 |
|----------------|------|
| `< 640px` (sm) | 전체 너비, 수직 배치 |
| `640px - 1024px` (md-lg) | 기본 레이아웃 |
| `> 1024px` (xl) | 최대 너비 제한 |

### 접근성 (A11y)

| 항목 | 구현 |
|------|------|
| **Role** | `role="{{role}}"` |
| **Label** | `aria-label="{{description}}"` |
| **Disabled** | `aria-disabled="true"` |
| **Loading** | `aria-busy="true"` |
| **키보드** | Enter/Space로 활성화 |
| **포커스** | Tab으로 이동, 시각적 표시 |

### 디자인 토큰

```css
/* Colors */
--component-bg: var(--color-surface);
--component-border: var(--color-border);
--component-text: var(--color-text-primary);

/* Spacing */
--component-padding-x: var(--spacing-4);
--component-padding-y: var(--spacing-3);
--component-gap: var(--spacing-2);

/* Effects */
--component-radius: var(--radius-md);
--component-shadow: var(--shadow-sm);
--component-transition: all 150ms ease;
```

### 사용 예시

```vue
<template>
  <!-- 기본 사용 -->
  <{{ComponentName}} title="제목" />

  <!-- Variant 적용 -->
  <{{ComponentName}} title="제목" variant="outlined" />

  <!-- 로딩 상태 -->
  <{{ComponentName}} title="제목" :loading="isLoading" />

  <!-- 이벤트 핸들링 -->
  <{{ComponentName}}
    title="제목"
    @action="handleAction"
  />
</template>
```

### 구현 체크리스트

- [ ] 기본 구조 구현
- [ ] Props 정의 (TypeScript)
- [ ] 모든 Variants 구현
- [ ] 모든 States 구현
- [ ] 반응형 동작 구현
- [ ] 접근성 속성 추가
- [ ] 키보드 네비게이션
- [ ] 단위 테스트 작성
- [ ] Storybook 스토리 작성
