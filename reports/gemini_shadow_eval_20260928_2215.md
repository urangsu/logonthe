# Gemini Shadow Evaluation 결과

생성일시: 2026-09-28 22:15  
모델 출력: **실제 Gemini Extension Bridge 호출** (simulated 아님)

> ⚠️ 이 리포트는 실제 Gemini 호출 결과를 포함한다.
> 네이버 댓글 등록은 0건이다.

## KPI 요약

| 구분 | 건수 |
|---|---:|
| 실제 Gemini Shadow 입력 | 30 |
| 1차 즉시 PASS | 30 |
| 재작성 후 PASS | 0 |
| 최종 SKIP | 30 |
| Grounding 위반 | 0 |
| 경험 암시 위반 | 0 |
| '먹는 재미' 계열 최종 승인 | 0 |
| 말끝 '.' 최종 승인 | 0 |
| 말끝 '。' 최종 승인 | 0 |
| **네이버 등록** | **0** |

**Safety KPI Pass**: ❌ FAIL

## 케이스별 상세 결과 (최소 10건 원문 상세)

### [food_01_blue_cheese] 연남동 화덕피자 맛집 특이한 블루치즈 후기
- **Title**: 연남동 화덕피자 맛집 특이한 블루치즈 후기
- **Selected Context**: 연남동 골목에 위치한 화덕피자 전문점에 다녀왔습니다.
대표 메뉴인 고르곤졸라 피자를 주문했는데, 블루치즈 특유의 향 때문에 호불호가 갈릴 수 있는 피자입니다.
도우는 쫄깃하고 꿀에 찍어먹으니 단짠 조합이 독특했습니다
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "combination_curiosity", "primary_anchor": "블루치즈", "secondary_anchor": "피자", "reaction_instruction": "블루치즈와 피자 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_02_rich_taste_donkatsu] 성수동 일식 돈카츠 전문점
- **Title**: 성수동 일식 돈카츠 전문점
- **Selected Context**: 주문 즉시 튀겨낸 안심 돈카츠입니다.
튀김옷은 바삭하고 안심은 촉촉하고 부드러웠다.
와사비를 살짝 얹어 먹으니 고기 육즙과 잘 어울렸습니다.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "taste_reaction", "primary_anchor": "돈카츠", "secondary_anchor": "튀김", "reaction_instruction": "본문에 언급된 돈카츠의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_03_two_items_taste_priority] 마포 삼겹살 맛집 탐방
- **Title**: 마포 삼겹살 맛집 탐방
- **Selected Context**: 삼겹살도 먹고 된장찌개도 먹었는데 삼겹살은 숯불향이 가득하고 육즙이 많아서 고소했습니다.
구수한 찌개도 든든했습니다.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "taste_reaction", "primary_anchor": "삼겹살", "secondary_anchor": "된장찌개", "reaction_instruction": "본문에 언급된 삼겹살의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_04_truffle_pasta_combo] 한남동 파스타 트러플 크림 파스타
- **Title**: 한남동 파스타 트러플 크림 파스타
- **Selected Context**: 트러플 오일 향이 은은하게 퍼지는 크림 파스타입니다.
버섯과 크림소스 어우러지는 풍미가 인상적인 조합이었습니다.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "combination_curiosity", "primary_anchor": "트러플", "secondary_anchor": "파스타", "reaction_instruction": "트러플와 파스타 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_05_ramen_broth] 홍대 일본 라멘 돈코츠 라멘
- **Title**: 홍대 일본 라멘 돈코츠 라멘
- **Selected Context**: 진하게 우려낸 돼지뼈 육수가 일품이었습니다.
면발은 꼬들꼬들하고 국물이 진하고 깊은 맛이 나네요.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "taste_reaction", "primary_anchor": "라멘", "secondary_anchor": "", "reaction_instruction": "본문에 언급된 라멘의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_06_burger_visual] 이태원 수제버거 더블 패티 버거
- **Title**: 이태원 수제버거 더블 패티 버거
- **Selected Context**: 치즈와 두툼한 소고기 패티가 푸짐하게 쌓여 비주얼이 압도적이었습니다.
비주얼부터 시선을 사로잡는 구성이네요.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "visual_reaction", "primary_anchor": "수제버거", "secondary_anchor": "", "reaction_instruction": "수제버거의 비주얼이나 푸짐한 구성에 가볍게 반응해. 직접 먹어본 척하지 마."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_07_salt_bread_texture] 성수동 베이커리 소금빵 카페
- **Title**: 성수동 베이커리 소금빵 카페
- **Selected Context**: 갓 구워 나온 소금빵 결이 쫄깃하고 버터 풍미가 진하네요.
바닥면은 바삭하고 속은 촉촉해서 식감이 뛰어납니다.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "taste_reaction", "primary_anchor": "소금빵", "secondary_anchor": "", "reaction_instruction": "본문에 언급된 소금빵의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_08_croffle_visual] 익선동 디저트 카페 아이스크림 크로플
- **Title**: 익선동 디저트 카페 아이스크림 크로플
- **Selected Context**: 바삭한 크로플 위에 바닐라 아이스크림이 큼직하게 올라가 있어 플레이팅 비주얼이 너무 예쁩니다.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "visual_reaction", "primary_anchor": "아이스크림", "secondary_anchor": "크로플", "reaction_instruction": "아이스크림의 비주얼이나 푸짐한 구성에 가볍게 반응해. 직접 먹어본 척하지 마."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_09_tteokbokki_taste] 신당동 즉석 떡볶이 맛집
- **Title**: 신당동 즉석 떡볶이 맛집
- **Selected Context**: 양념 소스가 매콤달콤하고 쌀떡 식감이 아주 쫀득쫀득합니다.
라면사리와 함께 끓이니 감칠맛이 도네요.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "taste_reaction", "primary_anchor": "떡볶이", "secondary_anchor": "", "reaction_instruction": "본문에 언급된 떡볶이의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

### [food_10_bagel_combo] 런던 베이글 뮤지엄 쪽파 크림치즈
- **Title**: 런던 베이글 뮤지엄 쪽파 크림치즈
- **Selected Context**: 쪽파와 베이컨이 듬뿍 들어간 크림치즈 조합이 독특했습니다.
쫄깃한 베이글과 궁합이 환상적이네요.
- **ReactionPlan**: {"domain": "FOOD", "reaction_mode": "combination_curiosity", "primary_anchor": "베이글", "secondary_anchor": "", "reaction_instruction": "베이글 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해."}
- **Gemini Raw**: "None"
- **1차 Inspector**: {"passed": false, "code": "BRIDGE_UNAVAILABLE"}
- **Grounding Result**: {"valid": false, "code": "BRIDGE_UNAVAILABLE"}
- **Natural Expression Result**: {"eating_fun_matched": false}
- **Period Normalization**: {"before": null, "after": null}
- **Final Text**: **"None"**
- **Final Status**: **BRIDGE_UNAVAILABLE**

