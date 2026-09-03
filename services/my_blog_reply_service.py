import json
import re
import time
import uuid
from typing import List, Dict, Any, Optional
from services.my_blog_comment_thread_collector import BlogCommentNode
from src.logger import logger


class MyBlogReplyService:
    """
    내 블로그 답글 생성 서비스:
    - 작성자 입장의 답글 프롬프트 빌더
    - 10~15개 단위 Batch 요청 및 correlation
    - JSON 배열 파싱 및 NEED_MORE_CONTEXT 처리
    """

    @classmethod
    def build_batch_prompt(
        cls,
        post_title: str,
        post_excerpt: str,
        target_comments: List[Dict[str, str]],
    ) -> str:
        """
        작업지시서 명세에 따른 답글 생성 배치 프롬프트
        """
        formatted_comments = []
        for idx, c in enumerate(target_comments, 1):
            c_no = c.get("comment_no", f"cmt_{idx}")
            nick = c.get("nickname", "독자")
            text = c.get("text", "").replace("\n", " ")
            formatted_comments.append(f'- [{c_no}] ({nick}): {text}')

        comments_block = "\n".join(formatted_comments)

        prompt = f"""너는 네이버 블로그 작성자가 자기 글에 달린 댓글에 답글을 쓰는 도우미야

아래 블로그 글은 내가 작성한 글이고 아래 댓글은 독자가 남긴 댓글이야
각 댓글의 의미에 맞춰 작성자 입장에서 자연스러운 답글 한 개를 만들어

원칙:
- 각 댓글의 의도를 파악하고 맞춤형으로 답해:
  * 단순 칭찬(PRAISE): 가벼운 감사 + 기분 좋은 공감 한마디
  * 본인 경험 공유(EXPERIENCE): 그 경험과 공감에 자연스럽게 맞장구
  * 질문(QUESTION / INFO_REQUEST): 제공된 글 본문으로 확인 가능한 내용만 명확히 답변 (답을 모르면 status를 NEED_MORE_CONTEXT로 설정)
  * 일상/소통(JOKE_LIGHT / OTHER): 친근하고 자연스럽게 반응
- 매번 똑같이 '감사합니다'로 시작하지 마
- 닉네임을 매번 억지로 부르지 마
- 자동응답기처럼 보이는 획일적인 문장을 피하고 다양하게 표현해
- 작성자인 내가 직접 경험한 글 내용은 말할 수 있지만 본문에 없는 사실은 지어내지 마
- 기본 20~65자, 필요하면 80자 정도까지

출력은 반드시 마크다운 코드블록(```json ... ```) 또는 순수 JSON 배열 형태로만 출력해:
[
  {{
    "comment_no": "원댓글 ID",
    "status": "REPLY",
    "reply": "추천 답글"
  }}
]

만약 질문인데 본문으로 답을 알 수 없거나 자료가 부족하면:
  {{
    "comment_no": "원댓글 ID",
    "status": "NEED_MORE_CONTEXT",
    "reply": ""
  }}

[내가 쓴 글 정보]
제목: {post_title}
본문 요약:
{post_excerpt if post_excerpt else '(본문 없음)'}

[답글을 작성해야 할 댓글 목록]
{comments_block}"""

        return prompt

    @classmethod
    def parse_batch_response(cls, raw_response: str) -> List[Dict[str, Any]]:
        """
        Gemini의 텍스트 응답에서 JSON 배열을 안전하게 추출 및 파싱
        """
        if not raw_response or not raw_response.strip():
            return []

        text = raw_response.strip()

        # 마크다운 json 블록 추출
        m = re.search(r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
        else:
            # 대괄호로 둘러싸인 JSON 배열 탐색
            m2 = re.search(r"(\[\s*\{.*?\}\s*\])", text, re.DOTALL)
            if m2:
                text = m2.group(1).strip()

        try:
            parsed = json.loads(text, strict=False)
            if isinstance(parsed, list):
                results = []
                for item in parsed:
                    if isinstance(item, dict) and "comment_no" in item:
                        results.append({
                            "comment_no": str(item.get("comment_no")),
                            "status": str(item.get("status", "REPLY")),
                            "reply": str(item.get("reply", "")).strip()
                        })
                return results
        except Exception as e:
            logger.log(f"[REPLY_SERVICE] JSON 파싱 실패: {e} (raw={raw_response[:100]}...)", "WARNING")

        return []

    @classmethod
    def generate_replies(
        cls,
        gemini_bridge: Any,
        post_title: str,
        post_excerpt: str,
        target_comments: List[Dict[str, str]],
        batch_size: int = 12,
        stop_event: Optional[Any] = None,
    ) -> Dict[str, Dict[str, str]]:
        """
        댓글 목록을 10~15개 단위 배치로 나누어 Gemini 확장 브리지로 요청하고 결과 취합
        """
        final_replies: Dict[str, Dict[str, str]] = {}
        if not gemini_bridge or not gemini_bridge.preflight().ready:
            logger.log("[REPLY_SERVICE] Gemini 확장 브리지가 연결되어 있지 않습니다.", "WARNING")
            return final_replies

        from services.gemini_extension_bridge import GeminiCommand, GeminiResultStatus

        total_batches = (len(target_comments) + batch_size - 1) // batch_size
        logger.log(f"[REPLY_SERVICE] 답글 생성 시작: 총 {len(target_comments)}개 댓글, {total_batches}개 배치")

        for b_idx in range(total_batches):
            if stop_event and stop_event.is_set():
                break

            batch_slice = target_comments[b_idx * batch_size : (b_idx + 1) * batch_size]
            prompt = cls.build_batch_prompt(post_title, post_excerpt, batch_slice)
            req_id = f"reply_{uuid.uuid4().hex[:8]}"

            # 1회 시도 + 실패 시 1회 retry
            success = False
            for attempt in range(2):
                if stop_event and stop_event.is_set():
                    break

                logger.log(f"[REPLY_SERVICE] 배치 {b_idx + 1}/{total_batches} 전송 (시도 {attempt + 1}, req_id={req_id})")
                cmd = GeminiCommand(
                    request_id=req_id,
                    post_key=f"my_reply_batch_{b_idx}",
                    prompt=prompt,
                    created_at=time.time(),
                    deadline_at=time.time() + 90.0,
                )
                gemini_bridge.publish(cmd)
                result = gemini_bridge.wait_for_result(cmd, stop_event=stop_event)

                if result and result.status == GeminiResultStatus.COMPLETED:
                    items = cls.parse_batch_response(result.text)
                    input_ids = {c["comment_no"] for c in batch_slice}
                    received_ids = set()
                    if items:
                        for it in items:
                            c_no = it.get("comment_no")
                            if c_no in input_ids:
                                final_replies[c_no] = {
                                    "status": it.get("status", "REPLY"),
                                    "reply": it.get("reply", "")
                                }
                                received_ids.add(c_no)
                        missing_ids = input_ids - received_ids
                        if not missing_ids or attempt >= 1:
                            logger.log(f"[REPLY_SERVICE] 배치 {b_idx + 1} 완료: {len(received_ids)}/{len(input_ids)} 반영")
                            success = True
                            break
                        else:
                            logger.log(f"[REPLY_SERVICE] 배치 {b_idx + 1} 누락 항목 {len(missing_ids)}개 감지 -> 누락분만 재요청", "WARNING")
                            batch_slice = [c for c in batch_slice if c["comment_no"] in missing_ids]
                            prompt = cls.build_batch_prompt(post_title, post_excerpt, batch_slice)
                            req_id = f"reply_{uuid.uuid4().hex[:8]}"
                            continue
                    else:
                        logger.log(f"[REPLY_SERVICE] 배치 {b_idx + 1} 응답 파싱 실패 -> 재시도", "WARNING")
                        req_id = f"reply_{uuid.uuid4().hex[:8]}"
                else:
                    logger.log(f"[REPLY_SERVICE] 배치 {b_idx + 1} 대기 실패 또는 타임아웃 -> 재시도", "WARNING")
                    req_id = f"reply_{uuid.uuid4().hex[:8]}"

            if not success:
                logger.log(f"[REPLY_SERVICE] 배치 {b_idx + 1} 최종 실패", "ERROR")

        return final_replies
