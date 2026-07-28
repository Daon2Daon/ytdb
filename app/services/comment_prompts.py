"""댓글 반응 분석 기본 프롬프트.

그룹 설정 prompts.comment_analysis_prompt가 비어 있을 때 사용한다.
분류 프롬프트는 출력 토큰을 최소화하기 위해 인덱스→라벨 압축 맵을 요구한다 —
건별 id/confidence를 되돌려받으면 출력이 3~4배로 늘고, 출력 단가가 입력의
8배가량이라 비용을 지배한다.
"""

DEFAULT_CLASSIFY_PROMPT = """다음은 YouTube 영상의 댓글 목록이다. 각 댓글의 감정을 분류하라.

라벨:
- p = 긍정 (호평, 감사, 지지, 공감)
- n = 부정 (비판, 실망, 반대, 분노)
- u = 중립 (질문, 정보 공유, 판단 불가, 무관한 내용)

반드시 아래 형식의 JSON 객체만 출력한다. 설명·주석·코드펜스를 붙이지 않는다.
키는 댓글 번호 문자열, 값은 p/n/u 중 하나다.

{"0":"p","1":"n","2":"u"}

댓글 목록:
"""

DEFAULT_INSIGHT_PROMPT = """다음은 YouTube 영상의 댓글을 감정별로 분류한 결과다.
각 카테고리별로 시청자 반응을 요약하고 인사이트를 도출하라.

반드시 아래 구조의 JSON 객체만 출력한다. 설명·코드펜스를 붙이지 않는다.
댓글이 없는 카테고리는 summary와 insights를 빈 문자열, key_points를 빈 배열로 둔다.

{
  "positive": {"summary": "2~3문장", "key_points": ["3~5개"], "insights": "2~3문장"},
  "negative": {"summary": "", "key_points": [], "insights": ""},
  "neutral":  {"summary": "", "key_points": [], "insights": ""}
}

summary는 해당 카테고리 댓글의 주된 내용을, insights는 콘텐츠 제작자가
참고할 만한 시사점을 쓴다. 모든 텍스트는 한국어로 작성한다.

분류된 댓글:
"""
