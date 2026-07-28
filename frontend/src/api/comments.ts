import { groupClient, rootApi } from './http'
import type {
  CommentAnalysisListItem,
  CommentAnalysisOut,
  CommentCredits,
  StartCommentAnalysisResponse,
} from './types'

export const commentApi = (slug: string) => ({
  get: (videoPk: number) =>
    groupClient(slug).get<CommentAnalysisOut>(`/videos/${videoPk}/comment-analysis`),
  list: (n = 20) =>
    groupClient(slug).get<CommentAnalysisListItem[]>(`/comment-analyses?limit=${n}`),
  start: (videoPk: number, limit: number) =>
    groupClient(slug).post<StartCommentAnalysisResponse>(
      `/videos/${videoPk}/comment-analysis`,
      { limit },
    ),
  startByUrl: (video_url: string, limit: number) =>
    groupClient(slug).post<StartCommentAnalysisResponse>('/comment-analysis/by-url', {
      video_url,
      limit,
    }),
})

// rootApi가 /api 프리픽스를 붙이므로 경로는 /me/...로 쓴다(me.ts와 동일).
export const creditsApi = {
  get: () => rootApi.get<CommentCredits>('/me/comment-credits'),
}
