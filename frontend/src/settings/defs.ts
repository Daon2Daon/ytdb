export type FieldType =
  | 'string' | 'int' | 'float' | 'textarea' | 'bool'
  | 'select' | 'model_select' | 'chatlist' | 'int_days' | 'int_hours'

export interface FieldDef {
  key: string
  label: string
  type?: FieldType
  secret?: boolean
  options?: string[]
  help?: string
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
  ],
  notification: [
    { key: 'enabled', label: '알림 활성화', type: 'bool' },
    { key: 'bot_token', label: '텔레그램 봇 토큰', secret: true },
    { key: 'chat_ids', label: 'Chat ID 목록', type: 'chatlist' },
    { key: 'parse_mode', label: 'parse_mode', type: 'select', options: ['HTML', 'MarkdownV2', 'None'], help: '일반적으로 HTML 권장' },
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
