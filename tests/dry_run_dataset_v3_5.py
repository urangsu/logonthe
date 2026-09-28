# -*- coding: utf-8 -*-
"""
v3.5 Reaction Pipeline 50-Item Dry-run Dataset & Evaluation Suite
Categories:
- Food/Cafe: 20
- Service/Beauty: 8
- Travel/Place: 8
- Product: 7
- Personal/Emotion: 7
Total: 50
"""

from typing import List, Dict, Any

FIXTURES_50: List[Dict[str, Any]] = [
    # =========================================================================
    # 1. FOOD & CAFE (20 items)
    # =========================================================================
    {
        "id": "food_01_blue_cheese",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "연남동 화덕피자 맛집 특이한 블루치즈 후기",
        "body": "연남동 골목에 위치한 화덕피자 전문점에 다녀왔습니다. 대표 메뉴인 고르곤졸라 피자를 주문했는데, 블루치즈 특유의 향 때문에 호불호가 갈릴 수 있는 피자입니다. 도우는 쫄깃하고 꿀에 찍어먹으니 단짠 조합이 독특했습니다.",
        "expected_mode": "combination_curiosity",
        "expected_anchor": "블루치즈",
        "sample_allowed_drafts": [
            "블루치즈 들어간 조합이라 어떤 맛일지 궁금하네요",
            "향이 진한 편이라 취향은 확실히 나뉘겠네요",
            "블루치즈 조합이 독특해서 한번 맛보고 싶네요",
        ],
        "sample_blocked_drafts": [
            "블루치즈 호불호 갈리는 거 너무 공감돼요",
            "저도 블루치즈는 호불호가 갈리더라고요",
            "그 맛 알죠 완전 대박인데",
        ],
    },
    {
        "id": "food_02_rich_taste_donkatsu",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "성수동 일식 돈카츠 전문점",
        "body": "주문 즉시 튀겨낸 안심 돈카츠입니다. 튀김옷은 바삭하고 안심은 촉촉하고 부드러웠다. 와사비를 살짝 얹어 먹으니 고기 육즙과 잘 어울렸습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "돈카츠",
        "sample_allowed_drafts": [
            "바삭한 튀김옷에 촉촉한 안심이라 식감이 제대로겠네요",
            "튀김옷이 바삭해 보여서 식감이 정말 궁금하네요",
        ],
        "sample_blocked_drafts": [
            "돈카츠 육즙 가득한 맛 너무 공감돼요",
            "저도 여기 돈카츠 자주 먹어요",
        ],
    },
    {
        "id": "food_03_two_items_taste_priority",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "마포 삼겹살 맛집 탐방",
        "body": "삼겹살도 먹고 된장찌개도 먹었는데 삼겹살은 숯불향이 가득하고 육즙이 많아서 고소했습니다. 구수한 찌개도 든든했습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "삼겹살",
        "sample_allowed_drafts": [
            "숯불향 가득하고 육즙이 많아서 정말 고소하겠네요",
        ],
        "sample_blocked_drafts": [
            "삼겹살에 된장찌개 조합 저도 엄청 좋아해요",
        ],
    },
    {
        "id": "food_04_truffle_pasta_combo",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "한남동 파스타 트러플 크림 파스타",
        "body": "트러플 오일 향이 은은하게 퍼지는 크림 파스타입니다. 버섯과 크림소스 어우러지는 풍미가 인상적인 조합이었습니다.",
        "expected_mode": "combination_curiosity",
        "expected_anchor": "트러플",
        "sample_allowed_drafts": [
            "트러플 향과 크림소스 조합이라 풍미가 궁금하네요",
        ],
        "sample_blocked_drafts": [
            "트러플 파스타 저도 진짜 좋아해요 완전 공감",
        ],
    },
    {
        "id": "food_05_ramen_broth",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "홍대 일본 라멘 돈코츠 라멘",
        "body": "진하게 우려낸 돼지뼈 육수가 일품이었습니다. 면발은 꼬들꼬들하고 국물이 진하고 깊은 맛이 나네요.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "라멘",
        "sample_allowed_drafts": [
            "진한 국물에 꼬들꼬들한 면발이라 식감이 매력적이겠네요",
        ],
        "sample_blocked_drafts": [
            "돈코츠 라멘 국물 진한 거 저도 완전 좋아해요",
        ],
    },
    {
        "id": "food_06_burger_visual",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "이태원 수제버거 더블 패티 버거",
        "body": "치즈와 두툼한 소고기 패티가 푸짐하게 쌓여 비주얼이 압도적이었습니다. 비주얼부터 시선을 사로잡는 구성이네요.",
        "expected_mode": "visual_reaction",
        "expected_anchor": "버거",
        "sample_allowed_drafts": [
            "두툼한 패티가 푸짐하게 쌓여서 비주얼이 대단하네요",
        ],
        "sample_blocked_drafts": [
            "저도 수제버거 비주얼 보고 감탄했어요 공감합니다",
        ],
    },
    {
        "id": "food_07_salt_bread_texture",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "성수동 베이커리 소금빵 카페",
        "body": "갓 구워 나온 소금빵 결이 쫄깃하고 버터 풍미가 진하네요. 바닥면은 바삭하고 속은 촉촉해서 식감이 뛰어납니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "소금빵",
        "sample_allowed_drafts": [
            "바닥은 바삭하고 속은 촉촉하다니 식감이 제대로겠어요",
        ],
        "sample_blocked_drafts": [
            "소금빵 겉바속촉 진리인 거 공감해요",
        ],
    },
    {
        "id": "food_08_croffle_visual",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "익선동 디저트 카페 아이스크림 크로플",
        "body": "바삭한 크로플 위에 바닐라 아이스크림이 큼직하게 올라가 있어 플레이팅 비주얼이 너무 예쁩니다.",
        "expected_mode": "visual_reaction",
        "expected_anchor": "크로플",
        "sample_allowed_drafts": [
            "아이스크림 올라간 플레이팅 비주얼이 정말 예쁘네요",
        ],
        "sample_blocked_drafts": [
            "크로플에 아이스크림 조합 저도 자주 먹어요",
        ],
    },
    {
        "id": "food_09_tteokbokki_taste",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "신당동 즉석 떡볶이 맛집",
        "body": "양념 소스가 매콤달콤하고 쌀떡 식감이 아주 쫀득쫀득합니다. 라면사리와 함께 끓이니 감칠맛이 도네요.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "떡볶이",
        "sample_allowed_drafts": [
            "매콤달콤한 소스에 쫀득한 떡 식감이라 군침 도네요",
        ],
        "sample_blocked_drafts": [
            "즉석 떡볶이 맛있는 거 완전 공감해요 저도 먹고싶어요",
        ],
    },
    {
        "id": "food_10_bagel_combo",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "런던 베이글 뮤지엄 쪽파 크림치즈",
        "body": "쪽파와 베이컨이 듬뿍 들어간 크림치즈 조합이 독특했습니다. 쫄깃한 베이글과 궁합이 환상적이네요.",
        "expected_mode": "combination_curiosity",
        "expected_anchor": "베이글",
        "sample_allowed_drafts": [
            "쪽파 크림치즈와 베이글 조합이라 어떤 맛일지 궁금하네요",
        ],
        "sample_blocked_drafts": [
            "쪽파 크림치즈 베이글 맛있는 거 저도 잘 알죠",
        ],
    },
    {
        "id": "food_11_espresso_crema",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "약수동 에스프레소 바 커피 탐방",
        "body": "두터운 크레마 층이 돋보이는 에스프레소입니다. 산미가 적당하고 쌉싸름하면서도 고소한 뒷맛이 남았습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "에스프레소",
        "sample_allowed_drafts": [
            "고소하면서도 쌉싸름한 풍미가 커피와 잘 어울리겠네요",
        ],
        "sample_blocked_drafts": [
            "에스프레소 묵직한 맛 저도 진짜 좋아해요",
        ],
    },
    {
        "id": "food_12_mala_special",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "건대 마라탕 특유의 얼얼한 맛",
        "body": "마라 특유의 알싸한 향과 얼얼한 매운맛이 제대로 살아있는 마라탕입니다. 소고기와 건두부 재료가 듬뿍 들어갔네요.",
        "expected_mode": "combination_curiosity",
        "expected_anchor": "마라",
        "sample_allowed_drafts": [
            "마라 특유의 알싸한 향이라 취향에 따라 색다른 맛이겠네요",
        ],
        "sample_blocked_drafts": [
            "마라탕 얼얼한 국물 저도 완전 사랑해요",
        ],
    },
    {
        "id": "food_13_eel_texture",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "파주 장어구이 몸보신 식당",
        "body": "숯불 위에 노릇노릇 구워낸 장어구이입니다. 겉은 바삭하고 속살은 담백하고 부드러워 입안에서 살살 녹았습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "장어구이",
        "sample_allowed_drafts": [
            "겉은 바삭하고 속살은 담백하다니 부드러운 식감이겠네요",
        ],
        "sample_blocked_drafts": [
            "장어구이 살살 녹는 맛 너무 공감됩니다",
        ],
    },
    {
        "id": "food_14_tendon_visual",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "샤로수길 텐동 바삭한 튀김덮밥",
        "body": "새우와 김, 단호박 튀김이 그릇 가득 수북하게 올려져 푸짐한 비주얼을 자랑합니다.",
        "expected_mode": "visual_reaction",
        "expected_anchor": "텐동",
        "sample_allowed_drafts": [
            "그릇 가득 수북하게 올려진 튀김 구성이 푸짐해 보여요",
        ],
        "sample_blocked_drafts": [
            "텐동 비주얼 대박인 거 저도 100% 공감해요",
        ],
    },
    {
        "id": "food_15_sashimi_fresh",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "노량진 수산시장 모듬회 포장",
        "body": "도톰하게 썰어낸 대광어와 연어회입니다. 신선도가 좋아서 찰지고 쫀득한 식감이 일품이었습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "회",
        "sample_allowed_drafts": [
            "도톰하게 썰어내서 찰지고 쫀득한 식감이 전해지네요",
        ],
        "sample_blocked_drafts": [
            "대광어 찰진 식감 저도 너무 좋아합니다",
        ],
    },
    {
        "id": "food_16_pancake_fluffy",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "가로수길 수플레 팬케이크 브런치",
        "body": "구름처럼 폭신폭신하고 부드러운 수플레 팬케이크입니다. 달콤한 시럽과 생크림이 촉촉하게 스며드네요.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "팬케이크",
        "sample_allowed_drafts": [
            "폭신폭신하고 부드러운 식감에 달콤한 시럽이라니 근사하네요",
        ],
        "sample_blocked_drafts": [
            "수플레 부드러운 맛 저도 잊지 못해요",
        ],
    },
    {
        "id": "food_17_pho_broth",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "을지로 베트남 쌀국수 맛집",
        "body": "오랜 시간 푹 고아낸 소고기 육수가 담백하고 깊은 맛을 냅니다. 아삭한 숙주와 부드러운 면발이 잘 어울립니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "쌀국수",
        "sample_allowed_drafts": [
            "담백하고 깊은 육수에 아삭한 숙주라 식감이 깔끔하겠어요",
        ],
        "sample_blocked_drafts": [
            "쌀국수 육수 깊은 맛 저도 엄청 애정해요",
        ],
    },
    {
        "id": "food_18_dakgalbi_taste",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "춘천 철판 닭갈비 볶음밥",
        "body": "매콤하고 진한 양념이 닭고기에 깊게 배어있습니다. 고소한 참기름을 두른 볶음밥까지 든든했습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "닭갈비",
        "sample_allowed_drafts": [
            "매콤한 양념이 깊게 배어있어 마무리 볶음밥까지 든든하겠네요",
        ],
        "sample_blocked_drafts": [
            "닭갈비 볶음밥 진리인 거 공감 백배입니다",
        ],
    },
    {
        "id": "food_19_macaron_texture",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "망원동 수제 마카롱 선물 세트",
        "body": "꼬끄가 쫀득쫀득하고 필링이 달지 않고 진해서 고소한 풍미가 돋보이는 마카롱이었습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "마카롱",
        "sample_allowed_drafts": [
            "꼬끄가 쫀득하고 필링이 고소해서 부담 없이 즐기기 좋겠네요",
        ],
        "sample_blocked_drafts": [
            "마카롱 쫀득한 거 저도 완전 취향저격이에요",
        ],
    },
    {
        "id": "food_20_hotpot_nurungji",
        "category": "FOOD",
        "expected_domain": "FOOD",
        "title": "안국동 솥밥 정식과 누룽지",
        "body": "도미 살을 얹은 영양 솥밥입니다. 밥을 덜어내고 뜨거운 물을 부어 만든 누룽지가 구수하고 담백했습니다.",
        "expected_mode": "taste_reaction",
        "expected_anchor": "솥밥",
        "sample_allowed_drafts": [
            "담백한 솥밥에 구수한 누룽지 마무리라 속이 편안하겠네요",
        ],
        "sample_blocked_drafts": [
            "솥밥 먹고 누룽지 구수한 거 진짜 공감해요",
        ],
    },

    # =========================================================================
    # 2. SERVICE & BEAUTY (8 items)
    # =========================================================================
    {
        "id": "service_01_hair_cut",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "청담동 미용실 레이어드컷 변신 후기",
        "body": "모발 손상이 심해서 고민하다가 원장님께 레이어드컷 시술을 받았습니다. 얼굴형에 맞게 층을 섬세하게 내주셔서 손질하기 편해졌습니다.",
        "expected_mode": "service_reaction",
        "expected_anchor": "레이어드컷",
        "sample_allowed_drafts": [
            "얼굴형에 맞춰 층을 섬세하게 잡아주셔서 라인이 깔끔하네요",
        ],
        "sample_blocked_drafts": [
            "레이어드컷 손질 편한 거 저도 너무 공감돼요",
        ],
    },
    {
        "id": "service_02_nail_art",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "강남 네일샵 봄맞이 그라데이션 네일",
        "body": "큐티클 케어도 꼼꼼하게 해주시고 파스텔톤 시럽 그라데이션 컬러가 깔끔하고 자연스럽게 완성되었습니다.",
        "expected_mode": "service_reaction",
        "expected_anchor": "네일",
        "sample_allowed_drafts": [
            "꼼꼼한 케어에 파스텔톤 그라데이션이 자연스럽게 어울리네요",
        ],
        "sample_blocked_drafts": [
            "그라데이션 네일 예쁜 거 저도 완전 공감합니다",
        ],
    },
    {
        "id": "service_03_skin_care",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "분당 피부관리 에스테틱 수분 진정 케어",
        "body": "건조했던 피부에 앰플을 듬뿍 흡수시켜 주셨습니다. 마스크팩과 데콜테 마사지까지 친절한 서비스로 힐링하고 왔네요.",
        "expected_mode": "service_reaction",
        "expected_anchor": "피부관리",
        "sample_allowed_drafts": [
            "차분한 진정 케어와 꼼꼼한 관리 덕분에 한결 편안해 보이네요",
        ],
        "sample_blocked_drafts": [
            "피부관리 받고 촉촉해진 느낌 저도 잘 알죠",
        ],
    },
    {
        "id": "service_04_pt_gym",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "역삼 헬스장 1:1 PT 체형 교정 운동",
        "body": "트레이너 선생님이 라운드숄더 자세를 정밀하게 분석해 주시고 스쿼트할 때 무릎 각도를 친절하게 교정해 주셨습니다.",
        "expected_mode": "service_reaction",
        "expected_anchor": "교정",
        "sample_allowed_drafts": [
            "자세와 각도를 꼼꼼히 짚어주셔서 바른 습관에 도움 되겠어요",
        ],
        "sample_blocked_drafts": [
            "스쿼트 무릎 교정 힘든 거 저도 격하게 공감해요",
        ],
    },
    {
        "id": "service_05_car_detailing",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "일산 프리미엄 손세차 광택 디테일링",
        "body": "휠에 낀 분진과 도장면 물때를 말끔하게 제거하고 유리막 코팅까지 깔끔한 마무리로 새 차처럼 반짝입니다.",
        "expected_mode": "service_reaction",
        "expected_anchor": "디테일링",
        "sample_allowed_drafts": [
            "구석구석 분진 제거와 코팅 마무리가 아주 정성스러워 보이네요",
        ],
        "sample_blocked_drafts": [
            "세차하고 반짝일 때 뿌듯한 기분 완전 공감합니다",
        ],
    },
    {
        "id": "service_06_pet_grooming",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "송파 애견 미용실 강아지 스파와 가위컷",
        "body": "미용 스트레스 없이 차분하게 탄산 스파와 위생 미용을 진행해 주셔서 아이가 편안해하는 모습이었습니다.",
        "expected_mode": "service_reaction",
        "expected_anchor": "미용",
        "sample_allowed_drafts": [
            "차분하게 스파와 미용을 진행해 주셔서 아이 표정이 편안해 보여요",
        ],
        "sample_blocked_drafts": [
            "강아지 미용 스트레스 걱정되는 거 너무 공감돼요",
        ],
    },
    {
        "id": "service_07_dental_scaling",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "여의도 치과 정기 검진 스케일링 후기",
        "body": "치위생사 선생님이 통증 없이 가글 마취 후 세심하게 치석을 제거해 주시고 칫솔질 방법도 상세히 설명해 주셨습니다.",
        "expected_mode": "service_reaction",
        "expected_anchor": "스케일링",
        "sample_allowed_drafts": [
            "세심한 설명과 배려 덕분에 편안하게 진료받으셨겠어요",
        ],
        "sample_blocked_drafts": [
            "스케일링 시원하고 무서운 거 저도 잘 압니다",
        ],
    },
    {
        "id": "service_08_massage_spa",
        "category": "SERVICE",
        "expected_domain": "SERVICE",
        "title": "잠실 아로마 전신 마사지 힐링 테라피",
        "body": "뭉친 어깨 근육을 은은한 라벤더 오일로 부드럽게 이완시켜 주어 쌓인 피로를 싹 풀고 왔습니다.",
        "expected_mode": "service_reaction",
        "expected_anchor": "마사지",
        "sample_allowed_drafts": [
            "은은한 오일 향과 함께 뭉친 피로를 부드럽게 풀기 좋겠네요",
        ],
        "sample_blocked_drafts": [
            "어깨 뭉쳤을 때 아로마 마사지 최고인 거 공감해요",
        ],
    },

    # =========================================================================
    # 3. TRAVEL & PLACE (8 items)
    # =========================================================================
    {
        "id": "place_01_weekend_jeju_travel",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "주말 제주 여행 코스 정리",
        "body": "이번 주말에 친구들과 함께 다녀온 제주도 바다 명소와 산책로 코스입니다. 협재 해변의 에메랄드빛 바다 풍경이 참 평화로웠습니다.",
        "expected_mode": "place_observation",
        "expected_anchor": "제주",
        "sample_allowed_drafts": [
            "에메랄드빛 바다와 산책로 풍경이 탁 트여 보여서 시원하네요",
        ],
        "sample_blocked_drafts": [
            "주말 제주도 여행 가고 싶은 마음 완전 공감돼요",
        ],
    },
    {
        "id": "place_02_child_jeju_trip",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "아이와 다녀온 제주 여행 후기",
        "body": "아이와 함께 제주도 해변 산책로를 걸으며 예쁜 바다 풍경을 눈에 담고 왔습니다. 올레길 코스가 완만해서 걷기 편했습니다.",
        "expected_mode": "place_observation",
        "expected_anchor": "제주",
        "sample_allowed_drafts": [
            "완만한 해변 산책로를 따라 펼쳐진 바다 풍경이 평온해 보이네요",
        ],
        "sample_blocked_drafts": [
            "아이 데리고 여행하기 힘든 거 저도 100% 공감합니다",
        ],
    },
    {
        "id": "place_03_hotel_stay_trip",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "강릉 호텔 1박 2일 여행 후기",
        "body": "바다가 한눈에 내려다보이는 강릉 호텔 숙소에서 1박 하며 힐링 관광하고 왔습니다. 통창으로 보이는 일출 풍경이 장관이었습니다.",
        "expected_mode": "place_observation",
        "expected_anchor": "강릉",
        "sample_allowed_drafts": [
            "통창으로 내려다보이는 바다와 일출 풍경이 참 멋지네요",
        ],
        "sample_blocked_drafts": [
            "강릉 바다 보고 힐링하는 기분 저도 잘 알아요",
        ],
    },
    {
        "id": "place_04_gyeongju_cherry_blossom",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "경주 보문단지 벚꽃 산책길 여행",
        "body": "보문호수 둘레길을 따라 만개한 벚꽃 터널이 이어져 있었습니다. 호수 물결과 흩날리는 꽃잎 풍경이 낭만적이네요.",
        "expected_mode": "place_observation",
        "expected_anchor": "경주",
        "sample_allowed_drafts": [
            "호수 둘레길 따라 만개한 벚꽃 터널 풍경이 참 운치 있네요",
        ],
        "sample_blocked_drafts": [
            "경주 벚꽃 예쁜 거 진짜 공감해요 저도 다녀왔거든요",
        ],
    },
    {
        "id": "place_05_busan_night_view",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "부산 광안리 해수욕장 야경 코스",
        "body": "광안대교에 불이 켜지며 반짝이는 불빛이 밤바다에 은은하게 비쳤습니다. 해변 버스킹 음악을 들으며 밤 산책을 즐겼습니다.",
        "expected_mode": "place_observation",
        "expected_anchor": "광안리",
        "sample_allowed_drafts": [
            "광안대교 불빛이 바다에 비치는 밤 풍경이 운치 있고 멋지네요",
        ],
        "sample_blocked_drafts": [
            "부산 야경 보면서 힐링하는 거 너무 공감돼요",
        ],
    },
    {
        "id": "place_06_danyang_paragliding",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "단양 패러글라이딩 활공장 전망대",
        "body": "소백산 능선과 굽이치는 남한강 물줄기가 발아래로 펼쳐졌습니다. 정상 카페에서 바라본 탁 트인 파노라마 뷰가 시원했습니다.",
        "expected_mode": "place_observation",
        "expected_anchor": "단양",
        "sample_allowed_drafts": [
            "산 능선과 강줄기가 한눈에 내려다보이는 전망이 시원하네요",
        ],
        "sample_blocked_drafts": [
            "패러글라이딩 탈 때 짜릿한 기분 저도 공감합니다",
        ],
    },
    {
        "id": "place_07_yeosu_cable_car",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "여수 밤바다 해상케이블카 관람",
        "body": "돌산대교와 여수 앞바다를 케이블카를 타고 가로질렀습니다. 하멜등대 주변 낭만포차 거리 풍경도 한눈에 들어왔습니다.",
        "expected_mode": "place_observation",
        "expected_anchor": "여수",
        "sample_allowed_drafts": [
            "케이블카에서 내려다보는 다리와 바다 야경 풍경이 근사하네요",
        ],
        "sample_blocked_drafts": [
            "여수 밤바다 케이블카 낭만적인 거 완전 공감해요",
        ],
    },
    {
        "id": "place_08_gapyeong_arboretum",
        "category": "PLACE",
        "expected_domain": "PLACE",
        "title": "가평 아침고요수목원 산책로 코스",
        "body": "울창한 잣나무 숲과 아기자기하게 꾸며진 야생화 정원 길을 걸었습니다. 피톤치드 향 가득한 숲길이 평화로웠습니다.",
        "expected_mode": "place_observation",
        "expected_anchor": "가평",
        "sample_allowed_drafts": [
            "잣나무 숲과 아기자기한 정원 산책로가 평화로워 보이네요",
        ],
        "sample_blocked_drafts": [
            "수목원 숲길 걸을 때 힐링되는 느낌 너무 공감가요",
        ],
    },

    # =========================================================================
    # 4. PRODUCT (7 items)
    # =========================================================================
    {
        "id": "prod_01_cordless_vacuum",
        "category": "PRODUCT",
        "expected_domain": "PRODUCT",
        "title": "무선청소기 흡입력 실사용 솔직 리뷰",
        "body": "본체 무게가 1.5kg으로 가벼워서 손목에 무리가 덜 갑니다. 틈새 노즐로 구석진 먼지까지 시원하게 청소할 수 있는 흡입력이었습니다.",
        "expected_mode": "feature_reaction",
        "expected_anchor": "청소기",
        "sample_allowed_drafts": [
            "가벼운 본체에 틈새 노즐 구성이라 손쉽게 관리하기 좋겠네요",
        ],
        "sample_blocked_drafts": [
            "무선청소기 손목 안 아픈 거 저도 완전 공감해요",
        ],
    },
    {
        "id": "prod_02_bluetooth_headphone",
        "category": "PRODUCT",
        "expected_domain": "PRODUCT",
        "title": "노이즈캔슬링 블루투스 헤드폰 개봉기",
        "body": "지하철 소음을 차단해주는 노이즈캔슬링 성능이 우수했습니다. 이어패드 쿠션감이 푹신해서 장시간 착용해도 편안하네요.",
        "expected_mode": "feature_reaction",
        "expected_anchor": "헤드폰",
        "sample_allowed_drafts": [
            "소음 차단 기능과 푹신한 쿠션 패드라 편안하게 쓰기 좋겠네요",
        ],
        "sample_blocked_drafts": [
            "노캔 헤드폰 쓰면 신세계인 거 저도 100% 공감합니다",
        ],
    },
    {
        "id": "prod_03_mechanical_keyboard",
        "category": "PRODUCT",
        "expected_domain": "PRODUCT",
        "title": "기계식 키보드 저소음 적축 타건감",
        "body": "통울림 없는 알루미늄 하우징에 정갈한 타건음이 인상적입니다. 키압이 가벼워서 타이핑 작업할 때 손가락 피로가 적었습니다.",
        "expected_mode": "feature_reaction",
        "expected_anchor": "키보드",
        "sample_allowed_drafts": [
            "정갈한 타건음과 부드러운 키감이라 타이핑하기 수월하겠어요",
        ],
        "sample_blocked_drafts": [
            "저소음 적축 타건감 손맛 좋은 거 저도 잘 알죠",
        ],
    },
    {
        "id": "prod_04_air_purifier",
        "category": "PRODUCT",
        "expected_domain": "PRODUCT",
        "title": "원룸 공기청정기 저소음 모드 리뷰",
        "body": "취침 모드로 켜두면 모터 소음이 거의 들리지 않아 숙면에 방해되지 않았습니다. 헤파필터 교체 방식도 간편했습니다.",
        "expected_mode": "feature_reaction",
        "expected_anchor": "공기청정기",
        "sample_allowed_drafts": [
            "조용한 취침 모드와 간편한 필터 방식이라 깔끔하게 쓰겠어요",
        ],
        "sample_blocked_drafts": [
            "원룸 공기청정기 소음 조용한 거 너무 공감돼요",
        ],
    },
    {
        "id": "prod_05_smartwatch_battery",
        "category": "PRODUCT",
        "expected_domain": "PRODUCT",
        "title": "스마트워치 배터리 사용 시간 사용기",
        "body": "완충 후 일반 사용 시 4일 이상 유지되는 긴 배터리가 마음에 들었습니다. 수면 측정 센서 반응도 신속하네요.",
        "expected_mode": "feature_reaction",
        "expected_anchor": "스마트워치",
        "sample_allowed_drafts": [
            "넉넉한 배터리 수명과 수면 측정 기능이 유용해 보이네요",
        ],
        "sample_blocked_drafts": [
            "스마트워치 배터리 오래가는 게 최고라는 거 공감해요",
        ],
    },
    {
        "id": "prod_06_power_bank",
        "category": "PRODUCT",
        "expected_domain": "PRODUCT",
        "title": "초고속 충전 대용량 보조배터리",
        "body": "PD 45W 고속 충전을 지원하여 노트북까지 충전할 수 있었습니다. 잔량 표시 LED 화면이 선명하게 보여 편리했습니다.",
        "expected_mode": "feature_reaction",
        "expected_anchor": "보조배터리",
        "sample_allowed_drafts": [
            "노트북 지원 출력과 선명한 잔량 화면이라 실용적이겠어요",
        ],
        "sample_blocked_drafts": [
            "고속 보조배터리 필수템인 거 저도 공감합니다",
        ],
    },
    {
        "id": "prod_07_camping_chair",
        "category": "PRODUCT",
        "expected_domain": "PRODUCT",
        "title": "경량 캠핑의자 접이식 릴렉스 체어",
        "body": "접었을 때 부피가 작아 트렁크에 수납하기 용이했습니다. 옥스포드 원단이 짱짱해서 기대앉았을 때 지지력이 안정적이었습니다.",
        "expected_mode": "feature_reaction",
        "expected_anchor": "캠핑의자",
        "sample_allowed_drafts": [
            "간편한 수납 부피와 짱짱한 원단 지지력이라 야외에서 든든하겠네요",
        ],
        "sample_blocked_drafts": [
            "캠핑의자 수납 부피 작은 게 최고인 거 공감돼요",
        ],
    },

    # =========================================================================
    # 5. PERSONAL & EMOTION (7 items)
    # =========================================================================
    {
        "id": "pers_01_exam_pass",
        "category": "PERSONAL",
        "expected_domain": "PERSONAL",
        "title": "기사 자격증 최종 합격 후기",
        "body": "3개월 동안 퇴근 후 도서관에서 공부한 끝에 드디어 합격자 명단에 이름을 올렸습니다. 그동안의 피로가 한순간에 씻겨 내려가는 기분입니다.",
        "expected_mode": "writer_feeling",
        "expected_anchor": "합격",
        "sample_allowed_drafts": [
            "퇴근 후 꾸준히 준비하셔서 이뤄낸 결실이라 정말 뿌듯하시겠어요",
        ],
        "sample_blocked_drafts": [
            "자격증 합격했을 때 짜릿한 기분 저도 완전 공감돼요",
        ],
    },
    {
        "id": "pers_02_birthday_letter",
        "category": "PERSONAL",
        "expected_domain": "PERSONAL",
        "title": "생일날 친구들의 손편지와 축하",
        "body": "오랜 친구들이 써준 정성스러운 손편지를 읽으며 마음이 뭉클해졌습니다. 소중한 사람들과 따뜻한 온기를 나눈 행복한 하루였습니다.",
        "expected_mode": "writer_feeling",
        "expected_anchor": "생일",
        "sample_allowed_drafts": [
            "친구분들의 정성 담긴 편지와 함께 마음 따뜻한 생일 보내셨네요",
        ],
        "sample_blocked_drafts": [
            "손편지 받고 뭉클한 감정 저도 잘 알 것 같아요",
        ],
    },
    {
        "id": "pers_03_recovery_day",
        "category": "PERSONAL",
        "expected_domain": "PERSONAL",
        "title": "감기 몸살 푹 쉬고 회복한 하루",
        "body": "며칠 동안 열과 오한으로 앓아누웠다가 따뜻한 죽을 먹고 푹 자고 일어났습니다. 가뿐해진 몸으로 따뜻한 차 한 잔을 마시니 살 것 같네요.",
        "expected_mode": "writer_feeling",
        "expected_anchor": "회복",
        "sample_allowed_drafts": [
            "푹 쉬시고 한결 가벼워지셨다니 정말 다행이네요",
        ],
        "sample_blocked_drafts": [
            "몸살 앓고 나면 건강 소중함 느끼는 거 공감합니다",
        ],
    },
    {
        "id": "pers_04_first_workday",
        "category": "PERSONAL",
        "expected_domain": "PERSONAL",
        "title": "새로운 직장 첫 출근 일기",
        "body": "낯선 환경과 새로운 팀원들 앞에서 긴장했지만 다들 따뜻하게 맞아주셨습니다. 설렘과 긴장이 교차했던 첫날을 무사히 마쳤습니다.",
        "expected_mode": "writer_feeling",
        "expected_anchor": "출근",
        "sample_allowed_drafts": [
            "긴장되는 첫 시작이었을 텐데 따뜻한 분위기 속에 잘 마무리하셨네요",
        ],
        "sample_blocked_drafts": [
            "첫 출근날 떨리고 긴장되는 거 저도 엄청 공감돼요",
        ],
    },
    {
        "id": "pers_05_moving_cleaning",
        "category": "PERSONAL",
        "expected_domain": "PERSONAL",
        "title": "새집 이사 짐 정리 끝내고 뿌듯한 밤",
        "body": "산더미 같던 이삿짐 박스를 모두 풀고 바닥까지 닦고 나니 밤이 깊었습니다. 깨끗해진 방 안에서 차 한잔 마시니 참 뿌듯합니다.",
        "expected_mode": "writer_feeling",
        "expected_anchor": "이사",
        "sample_allowed_drafts": [
            "정리 마치고 깔끔해진 공간을 바라보며 마시는 차 한 잔이 참 개운하겠어요",
        ],
        "sample_blocked_drafts": [
            "이사 짐정리 끝나고 뿌듯한 거 저도 완전 공감해요",
        ],
    },
    {
        "id": "pers_06_pet_birthday",
        "category": "PERSONAL",
        "expected_domain": "PERSONAL",
        "title": "반려견 초코 세 번째 생일 파티",
        "body": "수제 단호박 케이크를 만들어주고 꼬깔모자를 씌워 기념사진을 남겼습니다. 꼬리를 살랑이며 기뻐하는 모습에 온 가족이 웃음바다가 되었네요.",
        "expected_mode": "writer_feeling",
        "expected_anchor": "생일",
        "sample_allowed_drafts": [
            "정성껏 준비한 케이크와 함께 가족 모두 행복한 추억 남기셨네요",
        ],
        "sample_blocked_drafts": [
            "강아지 생일 챙겨줄 때 행복한 마음 너무 공감돼요",
        ],
    },
    {
        "id": "pers_07_weekend_home_cleaning",
        "category": "PERSONAL",
        "expected_domain": "PERSONAL",
        "title": "주말 대청소 끝내고 개운해진 기분",
        "body": "밀린 빨래를 널고 창문을 활짝 열어 환기시킨 뒤 먼지를 털어냈습니다. 향긋한 섬유유연제 냄새와 함께 상쾌한 주말을 시작합니다.",
        "expected_mode": "writer_feeling",
        "expected_anchor": "청소",
        "sample_allowed_drafts": [
            "창문 열고 환기하며 구석구석 정돈하고 나니 한결 상쾌하겠어요",
        ],
        "sample_blocked_drafts": [
            "주말 대청소 후 개운한 기분 저도 100% 공감합니다",
        ],
    },
]
