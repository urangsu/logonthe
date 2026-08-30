"""V13.1 Community Rhythm Chunk Banks.

Provides curated OPEN, REACTION, INTENT, and SOFT_END chunks for V13.1
20s community style comment composition.
"""

from typing import List, Tuple, Dict

# Opener chunks: (text, family_id)
# Empty string has high weighting so not all comments start with an exclamation.
OPENERS: List[Tuple[str, str]] = [
    ("", "none"),
    ("", "none"),
    ("", "none"),
    ("헐 ", "hul"),
    ("아 ", "ah"),
    ("와 ", "wa"),
    ("오 ", "oh"),
]

# Reaction chunks by category: list of (text_template, family_id)
# Templates can use {anchor} or stand as reaction fragments.
CATEGORY_REACTIONS: Dict[str, List[Tuple[str, str]]] = {
    "COMMON": [
        ("{anchor} 비쥬얼부터 딱이네", "visual_fit"),
        ("{anchor} 너무 좋은데요~", "very_good_tilde"),
        ("{anchor} 딱이네", "exact_fit"),
        ("{anchor} 좀 궁금한데~", "curious_tilde"),
        ("{anchor} 너무 이쁜데요~", "pretty_tilde"),
        ("{anchor} 분위기 너무 좋잖아요~", "mood_good_janayo"),
        ("{anchor} 이거 괜찮은데", "looks_nice"),
        ("{anchor} 느낌 너무 좋은데요~", "feeling_good_tilde"),
    ],
    "FOOD": [
        ("{anchor} 비쥬얼부터 딱이네", "food_visual_fit"),
        ("{anchor} 비주얼부터 딱이네", "food_visual_fit"),
        ("{anchor} 조합 너무 좋은데~", "food_combo_good"),
        ("{anchor} 이건 좀 먹어보고싶은데~", "food_want_eat"),
        ("{anchor} 색부터 맛있겠다", "food_color_tasty"),
        ("{anchor} 진짜 맛있어보이는데", "food_looks_delicious"),
        ("{anchor} 조합 딱 좋네요~", "food_combo_nice"),
        ("{anchor} 메뉴 너무 좋은데요~", "food_menu_good"),
    ],
    "CAFE": [
        ("{anchor} 뷰 뭐야 너무 이쁜데~", "cafe_view_what"),
        ("{anchor} 창가 자리 너무 좋은데요~", "cafe_window_seat"),
        ("{anchor} 색 너무 이쁜데~", "cafe_color_pretty"),
        ("{anchor} 분위기 딱 좋네", "cafe_mood_fit"),
        ("{anchor} 이건 저장해둬야겠다~", "cafe_save_intent"),
        ("{anchor} 디저트 비쥬얼 뭐야 너무 이쁜데요~", "cafe_dessert_pretty"),
        ("{anchor} 인테리어 느낌 너무 좋은데요~", "cafe_interior_mood"),
    ],
    "TRAVEL": [
        ("{anchor} 풍경 뭐야 너무 이쁜데~", "travel_scenery_what"),
        ("{anchor} 이건 저장해둬야지~", "travel_save_intent"),
        ("{anchor} 여기 한번 가보고싶다~", "travel_want_visit"),
        ("{anchor} 뷰 너무 좋은데요~", "travel_view_good"),
        ("{anchor} 이 길 걸어보고싶은데~", "travel_walk_road"),
        ("{anchor} 풍경 너무 평화로워 보여요~", "travel_peaceful"),
    ],
    "LIVING": [
        ("{anchor} 이거 이렇게 쓰는거였네", "living_how_to_use"),
        ("{anchor} 이건 저두 해보고싶어요~", "living_want_try"),
        ("{anchor} 이 방법 괜찮은데요~", "living_good_way"),
        ("{anchor} 이렇게 해두니까 훨씬 깔끔하네", "living_clean_setup"),
        ("{anchor} 정리해둔거 딱이네요~", "living_organize_fit"),
    ],
    "PARENTING": [
        ("{anchor} 이건 아이들이 좋아하겠는데~", "parenting_kids_like"),
        ("{anchor} 아이랑 가기 괜찮아보이네요~", "parenting_kids_visit"),
        ("{anchor} 이런 체험은 좀 재밌어보이는데", "parenting_fun_exp"),
        ("{anchor} 풍경이 너무 따뜻하고 평화로워 보여요~", "parenting_warm_view"),
    ],
    "HOBBY_GOODS": [
        ("{anchor} 실물 비쥬얼 딱이네", "hobby_visual_fit"),
        ("{anchor} 너무 귀여운데요~", "hobby_cute"),
        ("{anchor} 실물 너무 귀엽네요~", "hobby_cute_real"),
        ("{anchor} 디테일 뭐야 너무 이쁜데~", "hobby_detail_pretty"),
        ("{anchor} 이건 저장해둬야지~", "hobby_save_intent"),
        ("{anchor} 색감 너무 좋은데요~", "hobby_color_good"),
    ],
}

# Future Intent chunks: (text, family_id)
# 20~35% use curated variation "저두" / "저도"
INTENT_CHUNKS: List[Tuple[str, str]] = [
    ("저두 가보고싶어요~", "intent_visit_tilde"),
    ("저도 가보고싶어요~", "intent_visit_tilde_std"),
    ("저두 먹어보고싶어요~", "intent_eat_tilde"),
    ("저도 먹어보고싶어요~", "intent_eat_tilde_std"),
    ("이건 저장해둬야지~", "intent_save_tilde"),
    ("이건 저장해둬야겠다~", "intent_save_getda"),
    ("다음에 가면 이것도 봐야겠다~", "intent_next_time"),
    ("이 메뉴는 한번 먹어보고싶네요~", "intent_try_menu"),
    ("근처 갈 일 있으면 들러보고싶어요~", "intent_drop_by"),
    ("저두 한번 가봐야겠어요~", "intent_try_go_soft"),
]
