import { groupClient } from './http'

export interface SettingItem {
  key: string
  value: string | null
  value_type: 'string' | 'int' | 'float' | 'bool' | 'json'
  is_secret: boolean
  description?: string | null
}

export interface PromptPreset {
  preset_id: number
  name: string
  description: string | null
}

export function settingsApi(slug: string) {
  const c = groupClient(slug)
  return {
    get: (category: string) => c.get<SettingItem[]>(`/settings/${category}`),
    put: (category: string, items: SettingItem[]) =>
      c.put<SettingItem[]>(`/settings/${category}`, { items }),
    gatewayModels: () => c.get<string[]>(`/settings/ai_gateway/models`),
    promptPresets: () => c.get<PromptPreset[]>(`/settings/prompts/presets`),
  }
}
