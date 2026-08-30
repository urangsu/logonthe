"""V13.2 Community Rhythm Chunk Banks (Massive 20s Real Community Upgrade).

Provides curated OPEN, REACTION, INTENT, and SOFT_END chunks derived from
real 20s/30s online community (theqoo, instiz, real food/cafe/living blogs)
reactions without formal tone, periods, or AI summary boilerplate.
"""

from typing import List, Tuple, Dict

# Opener chunks: (text, family_id)
OPENERS: List[Tuple[str, str]] = [
    ("", "none"),
    ("", "none"),
    ("", "none"),
    ("헐 ", "hul"),
    ("와 ", "wa"),
    ("아 ", "ah"),
    ("오 ", "oh"),
    ("세상에 ", "sesang"),
    ("와 진짜 ", "wa_jinzza"),
    ("대박 ", "daebak_open"),
]

# Reaction chunks by category: list of (text_template, family_id)
CATEGORY_REACTIONS: Dict[str, List[Tuple[str, str]]] = {
    "COMMON": [
        ("{anchor} 비쥬얼부터 딱이네", "visual_fit"),
        ("{anchor} 너무 이쁜데요~", "pretty_tilde"),
        ("{anchor} 분위기 너무 좋잖아요~", "mood_good_janayo"),
        ("{anchor} 느낌 너무 좋은데요~", "feeling_good_tilde"),
        ("{anchor} 사진 보니까 너무 땡기네요~", "crave_photo"),
        ("{anchor} 이건 저장해둬야지~", "save_intent"),
        ("{anchor} 비쥬얼 진짜 미쳤네요~", "visual_crazy"),
        ("{anchor} 감성 완전 낭낭하네요~", "vibe_nangnang"),
        ("{anchor} 딱 제 스타일이에요~", "my_style"),
    ],
    "FOOD": [
        ("{anchor} 비쥬얼부터 딱이네", "food_visual_fit"),
        ("{anchor} 때깔 미쳤다 너무 맛있겠어요~", "food_color_insane"),
        ("{anchor} 진짜 맛있겠는데요 침고여요~", "food_salivate"),
        ("{anchor} 조합은 진짜 반칙인데요 완전 맛도리겠다~", "food_foul_combo"),
        ("{anchor} 사진 보니까 오늘 저녁으로 너무 땡기네요~", "food_crave_dinner"),
        ("{anchor} 비쥬얼 보니까 바로 군침도네요~", "food_mouthwater"),
        ("{anchor} 이건 맛없없 조합이네요~", "food_cant_fail"),
        ("{anchor} 윤기 좔좔 흐르는 것 좀 봐 대박이네요~", "food_glossy"),
        ("{anchor} 양도 진짜 푸짐하고 너무 먹음직스러워요~", "food_generous"),
        ("{anchor} 바삭바삭한 식감 사진으로도 다 느껴져요~", "food_crispy"),
        ("{anchor} 고기 두께 실화인가요 비쥬얼 대박이네요~", "food_thickness"),
        ("{anchor} 국물 색깔 무슨 일이야 너무 시원하겠어요~", "food_soup_refreshing"),
        ("{anchor} 하나 시켜서 맥주랑 먹으면 꿀맛이겠어요~", "food_with_beer"),
    ],
    "CAFE": [
        ("{anchor} 뷰 뭐야 너무 이쁜데~", "cafe_view_what"),
        ("{anchor} 디저트 비쥬얼 뭐야 너무 이쁜데요~", "cafe_dessert_pretty"),
        ("{anchor} 색감 무슨 일이야 너무 감성적이네요~", "cafe_color_vibe"),
        ("{anchor} 창가 자리 분위기 진짜 너무 좋잖아요~", "cafe_window_seat"),
        ("{anchor} 컵이랑 플레이팅 너무 귀엽고 감성있네요~", "cafe_plating_cute"),
        ("{anchor} 인테리어 진짜 감각적이네요~", "cafe_interior_sensible"),
        ("{anchor} 햇살 들어오는 분위기 너무 따뜻하고 이쁘네요~", "cafe_sunlight_warm"),
        ("{anchor} 층고 높고 탁 트여서 힐링하기 딱이겠어요~", "cafe_healing_space"),
        ("{anchor} 크림 올라간 것 좀 봐 완전 제 취향이에요~", "cafe_cream_taste"),
        ("{anchor} 디저트 라인업 대박이네 다음에 가면 들러야지~", "cafe_lineup_visit"),
        ("{anchor} 비쥬얼 보니까 당충전 제대로 되겠어요~", "cafe_sugar_charge"),
    ],
    "TRAVEL": [
        ("{anchor} 풍경 뭐야 진짜 그림같네요~", "travel_scenery_picture"),
        ("{anchor} 바다 뷰 탁 트인 것 좀 봐 힐링 제대로네요~", "travel_ocean_healing"),
        ("{anchor} 숙소 뷰 감성 대박이다 저두 가보고싶어요~", "travel_stay_vibe"),
        ("{anchor} 산책로 코스 너무 좋아 보이네요 저장해둬야지~", "travel_trail_save"),
        ("{anchor} 노을 지는 타이밍 진짜 예술이네요~", "travel_sunset_art"),
        ("{anchor} 힐링 여행지로 완전 딱이네요 사진 너무 이뻐요~", "travel_perfect_spot"),
        ("{anchor} 물 맑은 것 봐 보기만 해도 시원해지네요~", "travel_clear_water"),
        ("{anchor} 사진 보니까 당장 짐 싸서 떠나고 싶네요~", "travel_pack_bag"),
        ("{anchor} 분위기 진짜 평화롭고 고즈넉해서 좋네요~", "travel_peaceful_mood"),
    ],
    "LIVING": [
        ("{anchor} 정리 깔끔한 것 좀 봐 마음이 편안해지네요~", "living_clean_peace"),
        ("{anchor} 인테리어 센스 대박이네요 너무 감각적이에요~", "living_interior_sense"),
        ("{anchor} 꿀템 느낌 제대로네요 저두 사봐야겠어요~", "living_honey_item"),
        ("{anchor} 색감 톤온톤으로 맞춘 거 너무 이쁜데요~", "living_tone_on_tone"),
        ("{anchor} 공간 활용 진짜 잘하셨네요 완전 꿀팁이에요~", "living_space_hack"),
        ("{anchor} 소품 하나로 분위기 완전 확 사네요~", "living_prop_mood"),
        ("{anchor} 수납 아이디어 너무 좋다 저두 따라해볼게요~", "living_storage_idea"),
        ("{anchor} 디자인 깔끔하고 모던해서 너무 탐나네요~", "living_modern_covet"),
        ("{anchor} 살림 꿀팁 덕분에 하나 배워갑니다 저장해둘게요~", "living_tip_learned"),
    ],
    "RECIPE": [
        ("{anchor} 집에서 이렇게 뚝딱 만드시다니 대박이네요~", "recipe_quick_cook"),
        ("{anchor} 레시피 설명 너무 쉽게 잘해주셔서 따라하기 좋겠어요~", "recipe_easy_follow"),
        ("{anchor} 양념 조합 보기만 해도 군침도네요~", "recipe_sauce_combo"),
        ("{anchor} 오늘 저녁 메뉴 고민이었는데 바로 이거 해먹어야겠어요~", "recipe_dinner_pick"),
        ("{anchor} 완성된 비쥬얼 파는 것보다 더 훌륭하네요~", "recipe_better_than_store"),
        ("{anchor} 집에 있는 재료로 간단하게 만들기 딱이네요~", "recipe_simple_ingredients"),
        ("{anchor} 밥도둑 느낌 제대로네요 두공기 순삭각~", "recipe_rice_stealer"),
    ],
    "DAILY": [
        ("{anchor} 너무 귀엽다 보는 내내 미소지어지네요~", "daily_cute_smile"),
        ("{anchor} 소소하고 힐링되는 일상 글 너무 좋아요~", "daily_cozy_healing"),
        ("{anchor} 득템 축하드려요 너무 잘 어울리네요~", "daily_good_find"),
        ("{anchor} 주말 알차게 보내신 것 같아서 보기 너무 좋아요~", "daily_fruitful_weekend"),
        ("{anchor} 사진 분위기 너무 따뜻하고 몽글몽글해요~", "daily_warm_photo"),
        ("{anchor} 귀여운 거 보니까 오늘 피로가 싹 가시네요~", "daily_fatigue_gone"),
    ],
    "PARENTING": [
        ("{anchor} 아이들이 진짜 좋아하겠네요 너무 귀여워요~", "parenting_kids_love"),
        ("{anchor} 아이랑 나들이 가기 딱 좋아 보이네요~", "parenting_family_outing"),
        ("{anchor} 이런 체험 프로그램 너무 유익하고 재밌겠어요~", "parenting_fun_activity"),
        ("{anchor} 가족끼리 추억 만들기 완전 최고겠네요~", "parenting_family_memory"),
    ],
    "HOBBY_GOODS": [
        ("{anchor} 실물 비쥬얼 딱이네 너무 귀여워요~", "hobby_real_cute"),
        ("{anchor} 디테일 뭐야 너무 이쁜데요~", "hobby_detail_what"),
        ("{anchor} 색감 조합 너무 좋다 저두 탐나네요~", "hobby_combo_covet"),
        ("{anchor} 퀄리티 생각보다 진짜 좋아 보여요~", "hobby_high_quality"),
        ("{anchor} 소장 가치 100%네요 대박~", "hobby_collector_value"),
    ],
}

# Future Intent chunks: (text, family_id)
INTENT_CHUNKS: List[Tuple[str, str]] = [
    ("저두 가보고싶어요~", "intent_visit_tilde"),
    ("저도 가보고싶어요~", "intent_visit_tilde_std"),
    ("저두 먹어보고싶어요~", "intent_eat_tilde"),
    ("저도 먹어보고싶어요~", "intent_eat_tilde_std"),
    ("여긴 저장해둬야지~", "intent_save_tilde"),
    ("이건 저장해둬야겠다~", "intent_save_getda"),
    ("지도에 바로 저장했어요~", "intent_map_save"),
    ("오늘 저녁으로 너무 땡기네요~", "intent_crave_today"),
    ("다음에 가면 꼭 먹어봐야겠어요~", "intent_must_eat_next"),
    ("근처 갈 일 있으면 들러보고싶어요~", "intent_drop_by"),
    ("저두 장바구니 담아둡니다~", "intent_cart_save"),
    ("주말에 친구랑 가봐야겠어요~", "intent_weekend_friend"),
    ("이 메뉴는 한번 먹어보고싶네요~", "intent_try_menu"),
]
