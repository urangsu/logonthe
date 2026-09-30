# v3.5 Simulated Pipeline 5대 케이스 검증 결과

> ※ 본 리포트의 모델 출력은 테스트 fixture에 사전 정의된 simulated output이며
> 실제 Gemini 호출 결과가 아니다.

| 케이스 | 도메인 | 반응 모드 | 핵심 앵커 | Simulated 초안 | 재작성 | 최종 댓글 | Grounding | 최종 판정 |
|---|---|---|---|---|:---:|---|:---:|:---:|
| `food_01_blue_cheese` | `FOOD` | `combination_curiosity` | `블루치즈` | "블루치즈 호불호 갈리는 거 너무 공감돼요 저도 좋아해요..." | O | **"블루치즈 향에 꿀까지 곁들이는 조합이라 어떤 맛일지 궁금하네요"** | PASS | **APPROVED** |
| `food_02_rich_taste_donkatsu` | `FOOD` | `taste_reaction` | `돈카츠` | "바삭한 튀김옷에 촉촉한 안심이라 식감이 제대로겠네요..." | X | **"바삭한 튀김옷에 촉촉한 안심이라 식감이 제대로겠네요"** | PASS | **APPROVED** |
| `food_08_croffle_visual` | `FOOD` | `visual_reaction` | `아이스크림` | "아이스크림이 큼직하게 올라간 크로플 비주얼이 제대로네요..." | X | **"아이스크림이 큼직하게 올라간 크로플 비주얼이 제대로네요"** | PASS | **APPROVED** |
| `service_03_skin_care` | `SERVICE` | `service_reaction` | `마스크팩` | "앰플부터 마스크팩까지 이어지는 케어 구성이 꼼꼼하네요..." | X | **"앰플부터 마스크팩까지 이어지는 케어 구성이 꼼꼼하네요"** | PASS | **APPROVED** |
| `place_01_weekend_jeju_travel` | `PLACE` | `place_observation` | `코스` | "협재 해변의 에메랄드빛 바다 풍경이 평화로워 보이네요..." | X | **"협재 해변의 에메랄드빛 바다 풍경이 평화로워 보이네요"** | PASS | **APPROVED** |


## 상세 케이스별 반응 계획

### [food_01_blue_cheese] 연남동 화덕피자 맛집 특이한 블루치즈 후기
- **도메인**: `FOOD` / **모드**: `combination_curiosity`
- **핵심 앵커**: `블루치즈` (보조: `화덕피자`)
- **Instruction** (simulated): *"블루치즈와 화덕피자 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해."*
- **Simulated 초안**: "블루치즈 호불호 갈리는 거 너무 공감돼요 저도 좋아해요"
- **재작성 사유**: implied shared experience is forbidden: 공감돼요
- **최종 댓글**: **"블루치즈 향에 꿀까지 곁들이는 조합이라 어떤 맛일지 궁금하네요"**
- **Grounding Gate**: `ok` / unsupported=[]
- **최종 판정**: **APPROVED**
- **FOOD Relevance**: `True (tier_a_direct_anchor:블루치즈)`

### [food_02_rich_taste_donkatsu] 성수동 일식 돈카츠 전문점
- **도메인**: `FOOD` / **모드**: `taste_reaction`
- **핵심 앵커**: `돈카츠` (보조: `튀김`)
- **Instruction** (simulated): *"본문에 언급된 돈카츠의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마."*
- **Simulated 초안**: "바삭한 튀김옷에 촉촉한 안심이라 식감이 제대로겠네요"
- **최종 댓글**: **"바삭한 튀김옷에 촉촉한 안심이라 식감이 제대로겠네요"**
- **Grounding Gate**: `ok` / unsupported=[]
- **최종 판정**: **APPROVED**
- **FOOD Relevance**: `True (tier_a_direct_anchor:튀김)`

### [food_08_croffle_visual] 익선동 디저트 카페 아이스크림 크로플
- **도메인**: `FOOD` / **모드**: `visual_reaction`
- **핵심 앵커**: `아이스크림` (보조: `크로플`)
- **Instruction** (simulated): *"아이스크림의 비주얼이나 푸짐한 구성에 가볍게 반응해. 직접 먹어본 척하지 마."*
- **Simulated 초안**: "아이스크림이 큼직하게 올라간 크로플 비주얼이 제대로네요"
- **최종 댓글**: **"아이스크림이 큼직하게 올라간 크로플 비주얼이 제대로네요"**
- **Grounding Gate**: `ok` / unsupported=[]
- **최종 판정**: **APPROVED**
- **FOOD Relevance**: `True (tier_a_direct_anchor:아이스크림)`

### [service_03_skin_care] 분당 피부관리 에스테틱 수분 진정 케어
- **도메인**: `SERVICE` / **모드**: `service_reaction`
- **핵심 앵커**: `마스크팩` (보조: `수분 진정`)
- **Instruction** (simulated): *"마스크팩 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해."*
- **Simulated 초안**: "앰플부터 마스크팩까지 이어지는 케어 구성이 꼼꼼하네요"
- **최종 댓글**: **"앰플부터 마스크팩까지 이어지는 케어 구성이 꼼꼼하네요"**
- **Grounding Gate**: `ok` / unsupported=[]
- **최종 판정**: **APPROVED**

### [place_01_weekend_jeju_travel] 주말 제주 여행 코스 정리
- **도메인**: `PLACE` / **모드**: `place_observation`
- **핵심 앵커**: `코스` (보조: `제주`)
- **Instruction** (simulated): *"코스 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마."*
- **Simulated 초안**: "협재 해변의 에메랄드빛 바다 풍경이 평화로워 보이네요"
- **최종 댓글**: **"협재 해변의 에메랄드빛 바다 풍경이 평화로워 보이네요"**
- **Grounding Gate**: `ok` / unsupported=[]
- **최종 판정**: **APPROVED**

