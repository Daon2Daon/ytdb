export type FieldType =
  | 'string' | 'int' | 'float' | 'textarea' | 'bool'
  | 'select' | 'model_select' | 'chatlist' | 'int_days' | 'int_hours'
  | 'time' | 'timelist'

export interface FieldDef {
  key: string
  label: string
  type?: FieldType
  secret?: boolean
  options?: string[]
  help?: string
  showIf?: { key: string; equals: string | boolean }
}

export interface SettingCategory {
  key: string
  label: string
}

export const SETTING_CATEGORIES: SettingCategory[] = [
  { key: 'database', label: 'Database' },
  { key: 'ai_gateway', label: 'AI Gateway' },
  { key: 'polling', label: 'Monitoring' },
  { key: 'notification', label: 'Notification' },
  { key: 'prompts', label: 'Prompts' },
  { key: 'digest', label: 'Digest' },
]

export const SETTING_DEFS: Record<string, FieldDef[]> = {
  ai_gateway: [
    { key: 'base_url', label: '게이트웨이 Base URL', help: '예: http://litellm:4000 또는 http://<게이트웨이 호스트>:4000' },
    { key: 'api_key', label: 'API 키', secret: true, help: 'litellm 게이트웨이 인증 키' },
    { key: 'primary_model', label: '기본 모델 (경로 A)', type: 'model_select', help: '영상 분석 1차 호출 모델' },
    { key: 'fallback_model', label: '폴백 모델 (경로 B)', type: 'model_select', help: '기본 모델 실패 시 재시도 모델' },
    { key: 'temperature', label: 'temperature', type: 'float', help: '0~1 권장. 낮을수록 일관적' },
    { key: 'max_tokens', label: 'max_tokens', type: 'int', help: 'LLM 최대 출력 길이' },
  ],
  prompts: [
    { key: 'analysis_prompt', label: '분석 프롬프트', type: 'textarea' },
    { key: 'digest_prompt', label: '주간 리뷰 프롬프트', type: 'textarea' },
  ],
  database: [
    { key: 'host', label: '호스트' },
    { key: 'port', label: '포트', type: 'int' },
    { key: 'dbname', label: 'DB 이름' },
    { key: 'username', label: '사용자' },
    { key: 'password', label: '비밀번호', secret: true },
    { key: 'sslmode', label: 'sslmode', type: 'select', options: ['disable', 'prefer', 'require'], help: 'prefer 권장' },
  ],
  polling: [
    { key: 'youtube_api_key', label: 'YouTube API 키', secret: true },
    { key: 'window_hours', label: '최신 영상 수집 범위', type: 'int_days', help: '최근 N일 이내 업로드 영상만 수집' },
    { key: 'default_channel_interval_min', label: '새 영상 확인 주기', type: 'int_hours', help: '채널 기본 확인 주기(시간)' },
    { key: 'max_concurrent_channels', label: '동시 점검 채널 수', type: 'int' },
    { key: 'pending_analysis_interval_min', label: 'AI 분석 주기 (분)', type: 'int', help: '대기 영상 분석 스케줄 간격(분)' },
    { key: 'max_concurrent_analyses', label: 'AI 동시 요약 수', type: 'int' },
    { key: 'stats_refresh_days', label: '조회수 갱신 기간(일)', type: 'int', help: '게시 후 N일 이내 영상의 조회수·좋아요를 매일 갱신. 0이면 끔.' },
  ],
  notification: [
    { key: 'enabled', label: '알림 활성화', type: 'bool' },
    { key: 'bot_token', label: '텔레그램 봇 토큰', secret: true },
    { key: 'chat_ids', label: 'Chat ID 목록', type: 'chatlist' },
    { key: 'parse_mode', label: 'parse_mode', type: 'select', options: ['HTML', 'MarkdownV2', 'None'], help: '일반적으로 HTML 권장' },
    { key: 'send_mode', label: '발송 모드', type: 'select', options: ['immediate', 'scheduled'], help: 'immediate=분석 즉시 발송, scheduled=예약 시각에 일괄 발송' },
    { key: 'scheduled_times', label: '예약 발송 시각', type: 'timelist', help: 'HH:MM, 최대 10개. 각 시각마다 미발송분을 일괄 발송', showIf: { key: 'send_mode', equals: 'scheduled' } },
    { key: 'dispatch_scope', label: '발송 범위', type: 'select', options: ['after_activation', 'all'], help: 'after_activation=활성화 이후 게시분만, all=과거 분석분 포함 전체를 오래된 순으로 순차 발송', showIf: { key: 'send_mode', equals: 'scheduled' } },
    { key: 'scheduled_max_per_run', label: '회당 최대 발송 건수', type: 'int', help: '예약 회차·야간 보정 발송 1회당 발송 상한(1~50)' },
    { key: 'wait_between_messages_sec', label: '건별 대기(초)', type: 'int', help: '예약·야간 보정 발송 시 건 간 대기(스팸 방지)' },
    { key: 'quiet_hours_enabled', label: '야간 알림 제한', type: 'bool', help: '지정 시간대에는 발송하지 않고 보류 후 종료 시 자동 발송' },
    { key: 'quiet_hours_start', label: '제한 시작', type: 'time', showIf: { key: 'quiet_hours_enabled', equals: true } },
    { key: 'quiet_hours_end', label: '제한 종료', type: 'time', help: '종료가 시작보다 이르면 자정을 넘기는 구간', showIf: { key: 'quiet_hours_enabled', equals: true } },
    { key: 'timezone', label: '시간대', help: '야간·예약 판정 기준 (예: Asia/Seoul)' },
    { key: 'low_confidence_threshold', label: '저신뢰도 임계값', type: 'float', help: '0~1. 이 값 미만 분석은 알림 제목에 ⚠️ 표시' },
    { key: 'message_detail', label: '메시지 상세도', type: 'select', options: ['full', 'compact'], help: 'full=전체 분석·핵심주장·태그 포함, compact=한줄+짧은요약' },
  ],
  digest: [
    { key: 'enabled', label: '주간 리뷰 자동 생성', type: 'bool' },
    { key: 'period_weeks', label: '집계 기간(주)', type: 'int' },
    { key: 'schedule_day', label: '실행 요일', type: 'select', options: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] },
    { key: 'schedule_time', label: '실행 시각(HH:MM)' },
    { key: 'timezone', label: '시간대' },
    { key: 'telegram_enabled', label: '다이제스트 텔레그램 발송', type: 'bool' },
    { key: 'category', label: '카테고리 필터(선택)' },
  ],
}
