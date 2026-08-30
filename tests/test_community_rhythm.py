import unittest

from services.comments.community_rhythm import (
    COMMENT_POLICIES,
    CommunityRhythmPreset,
    FinalQualityGate,
)


class TestCommunityRhythmPolicy(unittest.TestCase):
    def test_exposes_preset_length_ranges(self):
        community = COMMENT_POLICIES[CommunityRhythmPreset.COMMUNITY]
        calm = COMMENT_POLICIES[CommunityRhythmPreset.CALM]

        self.assertEqual(len(FinalQualityGate.AI_SUMMARY_MACRO_PHRASES), len(set(FinalQualityGate.AI_SUMMARY_MACRO_PHRASES)))
        self.assertEqual((community.minimum, community.target_max, community.preferred_min, community.preferred_max, community.maximum), (16, 48, 18, 45, 100))
        self.assertEqual((calm.minimum, calm.target_max, calm.preferred_min, calm.preferred_max, calm.maximum), (20, 65, 20, 65, 100))
        self.assertEqual(FinalQualityGate.policy_for("community"), community)
        self.assertEqual(FinalQualityGate.policy_for("calm"), calm)

    def test_normalizes_nfc_without_other_mutation(self):
        result = FinalQualityGate.validate("이쁜 카페 분위기가 마음에 들어요", source="gemini")
        decomposed = FinalQualityGate.validate("이쁜 카페 분위기가 마음에 들어요", source="clipboard")

        self.assertTrue(result.valid)
        self.assertTrue(decomposed.valid)
        self.assertEqual(decomposed.normalized_text, "이쁜 카페 분위기가 마음에 들어요")
        self.assertEqual(decomposed.text, "이쁜 카페 분위기가 마음에 들어요")
        self.assertEqual(decomposed.source, "clipboard")

    def test_curated_natural_variants_fragments_and_tilde_are_allowed(self):
        for text in (
            "저두 이쁜 비쥬얼이 마음에 들어요",
            "비쥬얼이 참 이쁘고 분위기도 좋아 보여요~",
            "공간 분위기가 참 좋아 보여요",
        ):
            with self.subTest(text=text):
                self.assertTrue(FinalQualityGate.validate(text).valid)

    def test_rejects_period_anywhere(self):
        for text in ("분위기가 참 좋아요.", "비쥬얼. 참 좋아요", "가격 3.5가 눈에 들어와요"):
            with self.subTest(text=text):
                result = FinalQualityGate.validate(text)
                self.assertFalse(result.valid)
                self.assertEqual(result.code, "forbidden_period")

    def test_rejects_hard_max_and_reports_preferred_band(self):
        accepted = FinalQualityGate.validate("분위기가 정말 좋아 보여서 다음에 한번 들러보고 싶어요")
        self.assertTrue(accepted.valid)
        self.assertEqual(accepted.quality_band, "preferred")

        long_but_allowed = FinalQualityGate.validate("가" * 56)
        self.assertTrue(long_but_allowed.valid)
        self.assertEqual(long_but_allowed.quality_band, "long")
        self.assertLess(long_but_allowed.length_score, 1.0)

        too_long = FinalQualityGate.validate("가" * 101)
        self.assertFalse(too_long.valid)
        self.assertEqual(too_long.code, "length_exceeded")
        self.assertEqual(too_long.length, 101)

        calm_long_but_allowed = FinalQualityGate.validate("가" * 66, preset="calm")
        self.assertTrue(calm_long_but_allowed.valid)
        self.assertEqual(calm_long_but_allowed.quality_band, "long")

    def test_calm_preset_uses_its_own_minimum(self):
        short = FinalQualityGate.validate("분위기가 참 좋아요", preset="calm")
        self.assertFalse(short.valid)
        self.assertEqual(short.code, "length_below_minimum")

        accepted = FinalQualityGate.validate("차분한 공간 분위기가 편안해서 오래 머물고 싶어져요", preset="calm")
        self.assertTrue(accepted.valid)

    def test_rejects_formal_and_summary_macro_phrases(self):
        phrases = (
            "정말 합니다 참 마음에 들어요",
            "분위기입니다 참 좋아요",
            "구성이 됩니다 자연스럽네요",
            "사진이 보입니다 참 좋아요",
            "이런 느낌이 느껴집니다",
            "좋다고 생각됩니다",
            "그렇게 판단됩니다",
            "추천드립니다 여기 좋아요",
            "추천드려요 여기 좋아요",
            "인상적입니다 정말 좋아요",
            "인상적이네요 정말 좋아요",
            "유용합니다 정보가 많아요",
            "도움이 됩니다 좋은 글이에요",
            "좋을 것 같습니다 분위기가",
            "방문하고 싶습니다 다음에요",
            "먹어보고 싶습니다 다음에요",
            "같습니다 참 자연스러워요",
            "싶습니다 다음에 가고 싶어요",
            "전체적으로 구성이 참 좋아요",
            "무엇보다 분위기가 좋아요",
            "한눈에 들어오는 구성이네요 참 보기 좋아요",
            "정리가 잘 되어 보기 편해요",
            "사진 보니까 더 궁금해져요",
            "글 보니까 더 궁금해져요",
            "포스팅 보니까 더 궁금해져요",
            "전반적으로 분위기가 참 좋아요",
            "특히 공간 분위기가 참 좋아요",
            "따뜻하다는 점이 참 좋아요 그 느낌이",
            "이런 내용이라는 부분이 참 좋아요",
            "인상적인 공간이라 마음에 들어요",
            "알찬 구성이 참 좋아요",
            "유익한 정보가 많아 좋아요",
            "유용한 정보가 많아 좋아요",
            "구성이 참 좋아 보여요",
            "구성도 참 좋아 보여요",
            "조화가 참 좋아 보여요 정말 자연스러워요",
            "돋보이는 공간이라 마음에 들어요",
            "매력적인 분위기가 참 좋아요",
            "눈길을 끄는 공간이라 좋아요",
            "정성 가득 담긴 구성이 좋아요",
            "깔끔하게 정리되어 보기 좋아요",
            "잘 정리된 내용이라 보기 좋아요",
            "참고하기 좋은 내용이라 좋아요",
            "좋은 포스팅이라 마음에 들어요",
            "포스팅 잘 봤어요 참 좋아요",
            "오늘도 좋은 하루 보내세요",
            "제 블로그에도 놀러 와주세요",
            "소통해요 좋은 하루 보내세요",
            "서이추 좋은 하루 보내세요",
            "답방 좋은 하루 보내세요",
            "놀러 와주세요 좋은 하루 보내세요",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                result = FinalQualityGate.validate(phrase)
                self.assertFalse(result.valid)
                self.assertIn(result.code, {"formal_register", "banned_macro"})

    def test_rejects_laughter_emoticon_emoji_fake_experience_absolute_and_rude(self):
        cases = {
            "좋은 분위기네요 ㅎㅎ": "laughter_or_emoticon",
            "좋은 분위기네요 ㅋㅋ": "laughter_or_emoticon",
            "좋은 분위기네요 :)": "laughter_or_emoticon",
            "좋은 분위기네요 :P": "laughter_or_emoticon",
            "좋은 분위기네요 :-P": "laughter_or_emoticon",
            "좋은 분위기네요 😊": "emoji",
            "좋은 분위기네요 ㊗": "emoji",
            "저도 가봤는데 정말 좋았어요": "fake_experience",
            "저도 먹어봤는데 맛있어요": "fake_experience",
            "저도 써봤는데 편해요": "fake_experience",
            "저도 어제 다녀왔는데 정말 좋았어요": "fake_experience",
            "꼭 한번 방문해보세요 좋은 곳이에요": "absolute_or_pressure",
            "반드시 들러야 하는 곳이에요 정말 좋아요": "absolute_or_pressure",
            "무조건 만족할 곳이에요 정말 좋아요": "absolute_or_pressure",
            "이거 존나 좋아요 진짜": "rude_slang",
            "이거 병신같아요 정말": "rude_slang",
            "이거 개쩐다 정말": "rude_slang",
        }
        for text, code in cases.items():
            with self.subTest(text=text):
                result = FinalQualityGate.validate(text)
                self.assertFalse(result.valid)
                self.assertEqual(result.code, code)

    def test_validates_final_combined_text_independently_of_source(self):
        result = FinalQualityGate.validate_final_text(
            "저두 이쁜 비쥬얼이 마음에 들어서 다음에 한번 들러보고 싶어요",
            source="local",
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "local")


if __name__ == "__main__":
    unittest.main()
