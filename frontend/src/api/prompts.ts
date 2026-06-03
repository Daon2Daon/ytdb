import { settingsApi } from './settings'

export function promptApi(slug: string) {
  return {
    getAnalysisPrompt: async (): Promise<string> => {
      const items = await settingsApi(slug).get('prompts')
      return items.find((i) => i.key === 'analysis_prompt')?.value ?? ''
    },
  }
}
