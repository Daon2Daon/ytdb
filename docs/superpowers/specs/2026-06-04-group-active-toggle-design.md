# 그룹 활성/비활성 토글 설계

작성일: 2026-06-04
대상: ytdb

## 목적
그룹 단위로 자동화(폴링·분석·다이제스트·알림)를 일시정지/재개할 수 있게 한다.

## 현황 (이미 구현된 부분)
- `Group.is_active`(Boolean, 기본 True) 필드 존재.
- `PATCH /api/groups/{slug}` + `GroupUpdate` 스키마가 `is_active`를 이미 수용.
- 모든 백그라운드 잡이 `select(Group).where(Group.is_active.is_(True))`로 활성 그룹만 순회
  (master_poll, pending_analysis, digest_tick, notify_tick, 분석주기 계산).
- 프론트 `Group` 타입에 `is_active` 존재.

→ 비활성 시 모든 **자동화**가 멈춘다. 데이터 조회·수동 액션(즉시 분석/수동 발송)은 그대로 가능.

## 확정 결정
1. "멈춤" 범위 = 자동화만(기존 동작 유지). 수동 액션·조회는 계속 가능.
2. 현재 보고 있는 그룹을 꺼도 조회·재활성화 가능(라우팅은 slug 기준, is_active 무관).
3. 토글 위치 = 헤더의 「이름 수정」 버튼이 여는 모달(현재 `EditGroupModal`). 좌측 ⚙️ 설정 메뉴 아님.
4. 재개 시 catch-up은 기존 수집 윈도우(`window_hours`) 동작에 따름 — 본 작업 범위 밖(문서로만 안내).

## 변경 범위 (프론트엔드 전용)

### 1. `frontend/src/api/groups.ts`
`update`(부분 PATCH) 추가. 기존 `rename` 유지(하위호환). 
```ts
update: (slug: string, body: Partial<Pick<Group, 'name' | 'is_active'>>) =>
  rootApi.patch<Group>(`/groups/${slug}`, body),
```

### 2. `frontend/src/components/GroupModals.tsx` — `EditGroupModal`
- 모달 제목 "그룹 이름 수정" → "그룹 수정".
- 활성/비활성 토글 추가(`is_active`), 초기값 `activeGroup.is_active`.
- 저장 시 `groupApi.update(slug, { name, is_active })` 한 번으로 처리 후 `reloadGroups`.
- 비활성 선택 시 안내문구(예: "자동 수집·분석·알림이 중단됩니다. 데이터 조회와 수동 실행은 계속 가능합니다.").

### 3. `frontend/src/components/Layout.tsx` — 헤더
- 그룹 전환 select 옆에 현재 그룹이 비활성이면 배지 표시: `⏸ 일시정지`(회색).
- 드롭다운 옵션 라벨에도 비활성 그룹은 ` ⏸` 접미를 붙여 목록에서 식별 가능하게.

## 비목표
- 부분 정지(수집만 계속 등) 미지원.
- 백엔드 변경 없음.
- 재개 시 수집 윈도우 자동 확장 없음.

## 테스트
- 프론트 빌드/타입체크(tsc) 통과.
- 수동: 토글 OFF 저장 → 헤더 배지·드롭다운 표식 표시, 다음 틱부터 자동화 중단(잡로그에 해당 그룹 활동 없음). 토글 ON → 재개.
