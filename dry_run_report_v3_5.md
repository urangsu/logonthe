# v3.5 Reaction Pipeline 50-Item Dry-run Benchmark Report

| No | ID | Category | Domain | Mode | Primary Anchor | Needs More Context |
|---|---|---|---|---|---|---|
| 1 | food_01_blue_cheese | FOOD | FOOD | combination_curiosity | 블루치즈 | False |
| 2 | food_02_rich_taste_donkatsu | FOOD | FOOD | taste_reaction | 메뉴 | True |
| 3 | food_03_two_items_taste_priority | FOOD | FOOD | taste_reaction | 삼겹살 | False |
| 4 | food_04_truffle_pasta_combo | FOOD | FOOD | combination_curiosity | 트러플 | True |
| 5 | food_05_ramen_broth | FOOD | FOOD | taste_reaction | 라멘 | True |
| 6 | food_06_burger_visual | FOOD | FOOD | visual_reaction | 버거 | False |
| 7 | food_07_salt_bread_texture | FOOD | FOOD | taste_reaction | 소금빵 | False |
| 8 | food_08_croffle_visual | FOOD | FOOD | taste_reaction | 아이스크림 | False |
| 9 | food_09_tteokbokki_taste | FOOD | FOOD | taste_reaction | 메뉴 | False |
| 10 | food_10_bagel_combo | FOOD | FOOD | combination_curiosity | 베이글 | False |
| 11 | food_11_espresso_crema | FOOD | FOOD | taste_reaction | 커피 | True |
| 12 | food_12_mala_special | FOOD | FOOD | combination_curiosity | 마라 | False |
| 13 | food_13_eel_texture | FOOD | FOOD | taste_reaction | 숯불 | True |
| 14 | food_14_tendon_visual | FOOD | FOOD | taste_reaction | 메뉴 | False |
| 15 | food_15_sashimi_fresh | FOOD | FOOD | taste_reaction | 연어회 | False |
| 16 | food_16_pancake_fluffy | FOOD | FOOD | taste_reaction | 케이크 | True |
| 17 | food_17_pho_broth | FOOD | FOOD | taste_reaction | 쌀국수 | True |
| 18 | food_18_dakgalbi_taste | FOOD | FOOD | taste_reaction | 닭갈비 | False |
| 19 | food_19_macaron_texture | FOOD | FOOD | taste_reaction | 마카롱 | True |
| 20 | food_20_hotpot_nurungji | FOOD | FOOD | taste_reaction | 메뉴 | False |
| 21 | service_01_hair_cut | SERVICE | SERVICE | service_reaction | 미용실 | False |
| 22 | service_02_nail_art | SERVICE | SERVICE | service_reaction | 네일 | True |
| 23 | service_03_skin_care | SERVICE | SERVICE | service_reaction | 마사지 | False |
| 24 | service_04_pt_gym | SERVICE | SERVICE | service_reaction | 헬스장 | True |
| 25 | service_05_car_detailing | SERVICE | SERVICE | service_reaction | 세차 | True |
| 26 | service_06_pet_grooming | SERVICE | SERVICE | service_reaction | 미용실 | True |
| 27 | service_07_dental_scaling | SERVICE | SERVICE | service_reaction | 스케일링 | True |
| 28 | service_08_massage_spa | SERVICE | SERVICE | service_reaction | 마사지 | True |
| 29 | place_01_weekend_jeju_travel | PLACE | PLACE | place_observation | 여행 | True |
| 30 | place_02_child_jeju_trip | PLACE | PLACE | place_observation | 여행 | False |
| 31 | place_03_hotel_stay_trip | PLACE | PLACE | place_observation | 여행 | True |
| 32 | place_04_gyeongju_cherry_blossom | PLACE | PLACE | place_observation | 여행 | True |
| 33 | place_05_busan_night_view | PLACE | PLACE | place_observation | 코스 | True |
| 34 | place_06_danyang_paragliding | PLACE | PLACE | place_observation | 전망대 | False |
| 35 | place_07_yeosu_cable_car | PLACE | PLACE | place_observation | 풍경 | True |
| 36 | place_08_gapyeong_arboretum | PLACE | PLACE | place_observation | 코스 | True |
| 37 | prod_01_cordless_vacuum | PRODUCT | PRODUCT | feature_reaction | 청소기 | True |
| 38 | prod_02_bluetooth_headphone | PRODUCT | PRODUCT | feature_reaction | 헤드폰 | False |
| 39 | prod_03_mechanical_keyboard | PRODUCT | PRODUCT | feature_reaction | 키보드 | True |
| 40 | prod_04_air_purifier | PRODUCT | PRODUCT | feature_reaction | 공기청정기 | False |
| 41 | prod_05_smartwatch_battery | PRODUCT | PRODUCT | feature_reaction | 사용기 | False |
| 42 | prod_06_power_bank | PRODUCT | PRODUCT | feature_reaction | 노트북 | False |
| 43 | prod_07_camping_chair | PRODUCT | PRODUCT | feature_reaction | 의자 | False |
| 44 | pers_01_exam_pass | PERSONAL | PERSONAL | writer_feeling | 합격 | False |
| 45 | pers_02_birthday_letter | PERSONAL | PERSONAL | writer_feeling | 생일 | False |
| 46 | pers_03_recovery_day | PERSONAL | PERSONAL | writer_feeling | 회복 | False |
| 47 | pers_04_first_workday | PERSONAL | PERSONAL | writer_feeling | 일기 | False |
| 48 | pers_05_moving_cleaning | PERSONAL | PERSONAL | writer_feeling | 이사 | False |
| 49 | pers_06_pet_birthday | PERSONAL | PERSONAL | writer_feeling | 생일 | False |
| 50 | pers_07_weekend_home_cleaning | PERSONAL | GENERAL | detail_observation | 주말 | True |

## Detailed Reaction Plans

### [food_01_blue_cheese] 연남동 화덕피자 맛집 특이한 블루치즈 후기
- **Domain / Mode**: `FOOD` / `combination_curiosity`
- **Primary / Secondary Anchor**: `블루치즈` / `위치`
- **Evidence**: 조합/특수재료 근거(블루치즈와 위치)
- **Instruction**: 블루치즈와 위치 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해.

### [food_02_rich_taste_donkatsu] 성수동 일식 돈카츠 전문점
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `메뉴` / ``
- **Evidence**: 본문 맛/식감 언급(메뉴)
- **Instruction**: 본문에 언급된 메뉴의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_03_two_items_taste_priority] 마포 삼겹살 맛집 탐방
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `삼겹살` / `된장찌개`
- **Evidence**: 본문 맛/식감 언급(삼겹살)
- **Instruction**: 본문에 언급된 삼겹살의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_04_truffle_pasta_combo] 한남동 파스타 트러플 크림 파스타
- **Domain / Mode**: `FOOD` / `combination_curiosity`
- **Primary / Secondary Anchor**: `트러플` / `파스타`
- **Evidence**: 조합/특수재료 근거(트러플와 파스타)
- **Instruction**: 트러플와 파스타 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해.

### [food_05_ramen_broth] 홍대 일본 라멘 돈코츠 라멘
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `라멘` / ``
- **Evidence**: 본문 맛/식감 언급(라멘)
- **Instruction**: 본문에 언급된 라멘의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_06_burger_visual] 이태원 수제버거 더블 패티 버거
- **Domain / Mode**: `FOOD` / `visual_reaction`
- **Primary / Secondary Anchor**: `버거` / `수제버거`
- **Evidence**: 시각/구성 디테일(버거)
- **Instruction**: 버거의 비주얼이나 푸짐한 구성에 가볍게 반응해. 직접 먹어본 척하지 마.

### [food_07_salt_bread_texture] 성수동 베이커리 소금빵 카페
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `소금빵` / ``
- **Evidence**: 본문 맛/식감 언급(소금빵)
- **Instruction**: 본문에 언급된 소금빵의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_08_croffle_visual] 익선동 디저트 카페 아이스크림 크로플
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `아이스크림` / `크로플`
- **Evidence**: 본문 맛/식감 언급(아이스크림)
- **Instruction**: 본문에 언급된 아이스크림의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_09_tteokbokki_taste] 신당동 즉석 떡볶이 맛집
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `메뉴` / ``
- **Evidence**: 본문 맛/식감 언급(메뉴)
- **Instruction**: 본문에 언급된 메뉴의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_10_bagel_combo] 런던 베이글 뮤지엄 쪽파 크림치즈
- **Domain / Mode**: `FOOD` / `combination_curiosity`
- **Primary / Secondary Anchor**: `베이글` / ``
- **Evidence**: 조합/특수재료 근거(베이글)
- **Instruction**: 베이글 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해.

### [food_11_espresso_crema] 약수동 에스프레소 바 커피 탐방
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `커피` / ``
- **Evidence**: 본문 맛/식감 언급(커피)
- **Instruction**: 본문에 언급된 커피의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_12_mala_special] 건대 마라탕 특유의 얼얼한 맛
- **Domain / Mode**: `FOOD` / `combination_curiosity`
- **Primary / Secondary Anchor**: `마라` / `마라탕`
- **Evidence**: 조합/특수재료 근거(마라와 마라탕)
- **Instruction**: 마라와 마라탕 재료와 조합에 관찰자 입장에서 반응해. 먹어본 사람처럼 공감하지 말고 맛에 대한 궁금함이나 조합의 독특함을 표현해.

### [food_13_eel_texture] 파주 장어구이 몸보신 식당
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `숯불` / ``
- **Evidence**: 본문 맛/식감 언급(숯불)
- **Instruction**: 본문에 언급된 숯불의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_14_tendon_visual] 샤로수길 텐동 바삭한 튀김덮밥
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `메뉴` / ``
- **Evidence**: 본문 맛/식감 언급(메뉴)
- **Instruction**: 본문에 언급된 메뉴의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_15_sashimi_fresh] 노량진 수산시장 모듬회 포장
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `연어회` / `모듬회`
- **Evidence**: 본문 맛/식감 언급(연어회)
- **Instruction**: 본문에 언급된 연어회의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_16_pancake_fluffy] 가로수길 수플레 팬케이크 브런치
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `케이크` / ``
- **Evidence**: 본문 맛/식감 언급(케이크)
- **Instruction**: 본문에 언급된 케이크의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_17_pho_broth] 을지로 베트남 쌀국수 맛집
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `쌀국수` / ``
- **Evidence**: 본문 맛/식감 언급(쌀국수)
- **Instruction**: 본문에 언급된 쌀국수의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_18_dakgalbi_taste] 춘천 철판 닭갈비 볶음밥
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `닭갈비` / `볶음밥`
- **Evidence**: 본문 맛/식감 언급(닭갈비)
- **Instruction**: 본문에 언급된 닭갈비의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_19_macaron_texture] 망원동 수제 마카롱 선물 세트
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `마카롱` / ``
- **Evidence**: 본문 맛/식감 언급(마카롱)
- **Instruction**: 본문에 언급된 마카롱의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [food_20_hotpot_nurungji] 안국동 솥밥 정식과 누룽지
- **Domain / Mode**: `FOOD` / `taste_reaction`
- **Primary / Secondary Anchor**: `메뉴` / ``
- **Evidence**: 본문 맛/식감 언급(메뉴)
- **Instruction**: 본문에 언급된 메뉴의 맛이나 식감 디테일에 관찰자 입장에서 반응해. 본문에 없는 맛이나 식감은 지어내지 마.

### [service_01_hair_cut] 청담동 미용실 레이어드컷 변신 후기
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `미용실` / ``
- **Evidence**: 서비스 신호(미용실)
- **Instruction**: 미용실 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [service_02_nail_art] 강남 네일샵 봄맞이 그라데이션 네일
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `네일` / ``
- **Evidence**: 서비스 신호(네일)
- **Instruction**: 네일 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [service_03_skin_care] 분당 피부관리 에스테틱 수분 진정 케어
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `마사지` / `에스테틱`
- **Evidence**: 서비스 신호(마사지)
- **Instruction**: 마사지 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [service_04_pt_gym] 역삼 헬스장 1:1 PT 체형 교정 운동
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `헬스장` / ``
- **Evidence**: 서비스 신호(헬스장)
- **Instruction**: 헬스장 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [service_05_car_detailing] 일산 프리미엄 손세차 광택 디테일링
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `세차` / ``
- **Evidence**: 서비스 신호(세차)
- **Instruction**: 세차 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [service_06_pet_grooming] 송파 애견 미용실 강아지 스파와 가위컷
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `미용실` / ``
- **Evidence**: 서비스 신호(미용실)
- **Instruction**: 미용실 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [service_07_dental_scaling] 여의도 치과 정기 검진 스케일링 후기
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `스케일링` / ``
- **Evidence**: 서비스 신호(스케일링)
- **Instruction**: 스케일링 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [service_08_massage_spa] 잠실 아로마 전신 마사지 힐링 테라피
- **Domain / Mode**: `SERVICE` / `service_reaction`
- **Primary / Secondary Anchor**: `마사지` / `아로마`
- **Evidence**: 서비스 신호(마사지)
- **Instruction**: 마사지 서비스의 구성이나 진행 방식 디테일에 관찰자 입장에서 반응해.

### [place_01_weekend_jeju_travel] 주말 제주 여행 코스 정리
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `여행` / `명소`
- **Evidence**: 장소/여행 신호(여행)
- **Instruction**: 여행 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [place_02_child_jeju_trip] 아이와 다녀온 제주 여행 후기
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `여행` / `코스`
- **Evidence**: 장소/여행 신호(여행)
- **Instruction**: 여행 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [place_03_hotel_stay_trip] 강릉 호텔 1박 2일 여행 후기
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `여행` / `숙소`
- **Evidence**: 장소/여행 신호(여행)
- **Instruction**: 여행 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [place_04_gyeongju_cherry_blossom] 경주 보문단지 벚꽃 산책길 여행
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `여행` / `둘레길`
- **Evidence**: 장소/여행 신호(여행)
- **Instruction**: 여행 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [place_05_busan_night_view] 부산 광안리 해수욕장 야경 코스
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `코스` / `야경`
- **Evidence**: 장소/여행 신호(코스)
- **Instruction**: 코스 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [place_06_danyang_paragliding] 단양 패러글라이딩 활공장 전망대
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `전망대` / ``
- **Evidence**: 장소/여행 신호(전망대)
- **Instruction**: 전망대 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [place_07_yeosu_cable_car] 여수 밤바다 해상케이블카 관람
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `풍경` / `바다`
- **Evidence**: 장소/여행 신호(풍경)
- **Instruction**: 풍경 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [place_08_gapyeong_arboretum] 가평 아침고요수목원 산책로 코스
- **Domain / Mode**: `PLACE` / `place_observation`
- **Primary / Secondary Anchor**: `코스` / `산책로`
- **Evidence**: 장소/여행 신호(코스)
- **Instruction**: 코스 관련 풍경이나 코스, 공간 디테일에 관찰자 입장에서 반응해. 직접 가본 척하지 마.

### [prod_01_cordless_vacuum] 무선청소기 흡입력 실사용 솔직 리뷰
- **Domain / Mode**: `PRODUCT` / `feature_reaction`
- **Primary / Secondary Anchor**: `청소기` / ``
- **Evidence**: 제품 신호(청소기)
- **Instruction**: 제품의 청소기 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마.

### [prod_02_bluetooth_headphone] 노이즈캔슬링 블루투스 헤드폰 개봉기
- **Domain / Mode**: `PRODUCT` / `feature_reaction`
- **Primary / Secondary Anchor**: `헤드폰` / ``
- **Evidence**: 제품 신호(헤드폰)
- **Instruction**: 제품의 헤드폰 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마.

### [prod_03_mechanical_keyboard] 기계식 키보드 저소음 적축 타건감
- **Domain / Mode**: `PRODUCT` / `feature_reaction`
- **Primary / Secondary Anchor**: `키보드` / ``
- **Evidence**: 제품 신호(키보드)
- **Instruction**: 제품의 키보드 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마.

### [prod_04_air_purifier] 원룸 공기청정기 저소음 모드 리뷰
- **Domain / Mode**: `PRODUCT` / `feature_reaction`
- **Primary / Secondary Anchor**: `공기청정기` / ``
- **Evidence**: 제품 신호(공기청정기)
- **Instruction**: 제품의 공기청정기 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마.

### [prod_05_smartwatch_battery] 스마트워치 배터리 사용 시간 사용기
- **Domain / Mode**: `PRODUCT` / `feature_reaction`
- **Primary / Secondary Anchor**: `사용기` / `배터리`
- **Evidence**: 제품 신호(사용기)
- **Instruction**: 제품의 사용기 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마.

### [prod_06_power_bank] 초고속 충전 대용량 보조배터리
- **Domain / Mode**: `PRODUCT` / `feature_reaction`
- **Primary / Secondary Anchor**: `노트북` / ``
- **Evidence**: 제품 신호(노트북)
- **Instruction**: 제품의 노트북 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마.

### [prod_07_camping_chair] 경량 캠핑의자 접이식 릴렉스 체어
- **Domain / Mode**: `PRODUCT` / `feature_reaction`
- **Primary / Secondary Anchor**: `의자` / ``
- **Evidence**: 제품 신호(의자)
- **Instruction**: 제품의 의자 기능이나 디자인, 사용 디테일에 관찰자 입장에서 가볍게 반응해. 직접 써본 척하지 마.

### [pers_01_exam_pass] 기사 자격증 최종 합격 후기
- **Domain / Mode**: `PERSONAL` / `writer_feeling`
- **Primary / Secondary Anchor**: `합격` / ``
- **Evidence**: 일상/감정 신호(합격)
- **Instruction**: 작성자가 겪은 상황이나 마음에 가볍게 호응하거나 응원해. 내 경험을 덧붙이지 마.

### [pers_02_birthday_letter] 생일날 친구들의 손편지와 축하
- **Domain / Mode**: `PERSONAL` / `writer_feeling`
- **Primary / Secondary Anchor**: `생일` / `마음이`
- **Evidence**: 일상/감정 신호(생일)
- **Instruction**: 작성자가 겪은 상황이나 마음에 가볍게 호응하거나 응원해. 내 경험을 덧붙이지 마.

### [pers_03_recovery_day] 감기 몸살 푹 쉬고 회복한 하루
- **Domain / Mode**: `PERSONAL` / `writer_feeling`
- **Primary / Secondary Anchor**: `회복` / ``
- **Evidence**: 일상/감정 신호(회복)
- **Instruction**: 작성자가 겪은 상황이나 마음에 가볍게 호응하거나 응원해. 내 경험을 덧붙이지 마.

### [pers_04_first_workday] 새로운 직장 첫 출근 일기
- **Domain / Mode**: `PERSONAL` / `writer_feeling`
- **Primary / Secondary Anchor**: `일기` / ``
- **Evidence**: 일상/감정 신호(일기)
- **Instruction**: 작성자가 겪은 상황이나 마음에 가볍게 호응하거나 응원해. 내 경험을 덧붙이지 마.

### [pers_05_moving_cleaning] 새집 이사 짐 정리 끝내고 뿌듯한 밤
- **Domain / Mode**: `PERSONAL` / `writer_feeling`
- **Primary / Secondary Anchor**: `이사` / `뿌듯`
- **Evidence**: 일상/감정 신호(이사)
- **Instruction**: 작성자가 겪은 상황이나 마음에 가볍게 호응하거나 응원해. 내 경험을 덧붙이지 마.

### [pers_06_pet_birthday] 반려견 초코 세 번째 생일 파티
- **Domain / Mode**: `PERSONAL` / `writer_feeling`
- **Primary / Secondary Anchor**: `생일` / `반려견`
- **Evidence**: 일상/감정 신호(생일)
- **Instruction**: 작성자가 겪은 상황이나 마음에 가볍게 호응하거나 응원해. 내 경험을 덧붙이지 마.

### [pers_07_weekend_home_cleaning] 주말 대청소 끝내고 개운해진 기분
- **Domain / Mode**: `GENERAL` / `detail_observation`
- **Primary / Secondary Anchor**: `주말` / ``
- **Evidence**: 일반 본문 디테일
- **Instruction**: 본문에서 가장 눈에 띄는 구체적인 사실이나 디테일 하나에 솔직하고 가볍게 반응해.

