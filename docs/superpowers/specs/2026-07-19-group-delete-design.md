# 그룹 삭제 기능 설계

날짜: 2026-07-19
상태: 승인됨

## 배경

백엔드에는 `DELETE /api/groups/{slug}`가 이미 존재하고(`get_group_or_404`로 소유권 검사,
구독 해제 + 그룹 행 삭제, 제어 평면은 FK CASCADE 정리), 프론트엔드에는 삭제 UI가 전혀 없다.
또한 현재 삭제는 데이터 평면 스키마(`youtube_*`)를 드롭하지 않아 고아 스키마가 남는다.

## 결정 사항

- **데이터 처리**: 그룹 삭제 시 데이터 평면 스키마까지 완전 삭제(DROP SCHEMA CASCADE).
  단, 안전장치로 **자동 생성 패턴 스키마만** 드롭한다(아래 참조).
- **삭제 UX**: 그룹 수정 모달 하단 위험 구역 + 그룹 명칭 직접 입력 확인(GitHub 방식).

## 1. 백엔드 — 스키마 드롭

### `DataPlaneEngineManager.drop_schema(group)` (app/services/db_engine.py)

- `DROP SCHEMA IF EXISTS "{schema_name}" CASCADE` 실행.
- `_initialized` 캐시에서 `(server_sig, schema_name)` 키 제거 — 같은 이름으로 재생성 시
  `ensure_schema`가 스킵하지 않도록.
- 그룹 DB 설정이 없으면(`DBNotConfiguredError`) 스키마가 만들어진 적이 없으므로 조용히 건너뜀.

### 자동 생성 스키마 판별

일반 사용자 그룹의 스키마는 `youtube_u{userId}_{hex6}` 패턴으로 자동 생성된다
(`app/routers/groups.py` create_group). 스키마 드롭은 이 패턴
(`^youtube_u\d+_[0-9a-f]{6}$`)에 매칭될 때만 수행한다.

- 매칭: 스키마 드롭 + 그룹 행 삭제.
- 비매칭(레거시 `youtube_invest` 등 관리자 커스텀 스키마): 그룹 행만 삭제, 스키마 보존
  (현행 동작 유지). 프로덕션 데이터 오삭제 방지.

### 삭제 순서 (app/routers/groups.py delete_group)

1. 스키마 드롭(자동 생성 패턴일 때만, best-effort 아님 — 실패 시 500으로 요청 실패)
2. 구독 해제(`remove_group_subscriptions`)
3. 그룹 행 삭제 + 커밋

드롭 실패 시 그룹이 남으므로 재시도 가능(고아 스키마 방지).

## 2. 프론트엔드 — 삭제 UI

- `groupApi.remove(slug)` 추가 (`DELETE /groups/{slug}`).
- `EditGroupModal` 하단에 위험 구역 추가:
  - "그룹 삭제" 버튼(빨간 테두리) → 클릭 시 확인 단계로 전환.
  - 확인 단계: 경고 문구("수집된 영상·분석 데이터가 모두 영구 삭제됩니다. 되돌릴 수 없습니다.")
    + 그룹 명칭 입력 필드. 입력값이 그룹 명칭과 일치해야 삭제 버튼 활성화.
- 삭제 성공 시: `reloadGroups()` → 남은 그룹 중 첫 번째로 `navigate(/g/{slug}/)`,
  남은 그룹이 없으면 루트로 이동(그룹 없음 상태 화면).

## 3. 테스트

- 본인 그룹 삭제: 204, 그룹 행·설정 정리 확인.
- 타인 그룹 삭제: 404 (기존 소유권 체인).
- 자동 생성 패턴 판별 로직 단위 테스트(매칭/비매칭 케이스).
- 자동 생성 패턴 그룹 삭제 시 drop_schema 호출, 커스텀 스키마 그룹은 미호출.

## 범위 밖

- 소프트 삭제/휴지통, 삭제 유예 기간.
- 관리자 커스텀 스키마의 스키마 정리(수동 운영으로 대응).
