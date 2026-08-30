import unittest
import unicodedata

from services.comments.composer import LocalComposerV41
from services.comments.community_rhythm import FinalQualityGate
from tests.test_regression_corpus import TestRegressionCorpus100 as _RegressionCorpus
from tests.test_v13_1_corpus import TEST_CORPUS_50


class V132QualityAcceptanceTests(unittest.TestCase):
    @staticmethod
    def samples():
        first = [(title, excerpt) for title, excerpt, _cat in _RegressionCorpus.FIXTURES]
        second = [(item["title"], item["excerpt"]) for item in TEST_CORPUS_50]
        return first + second

    def setUp(self):
        LocalComposerV41.reset_history()

    def test_100_samples_have_grounded_anchor_and_structured_quality(self):
        samples = self.samples()
        self.assertEqual(len(samples), 100)
        for index, (title, excerpt) in enumerate(samples, 1):
            candidate, _confidence = LocalComposerV41.compose(title, excerpt)
            self.assertIsNotNone(candidate, f"sample {index}: {title}")
            context = unicodedata.normalize("NFC", f"{title} {excerpt}")
            self.assertIn(candidate.anchor, context, f"sample {index}: {candidate.body}")
            self.assertTrue(candidate.evidence_span)
            result = FinalQualityGate.validate_final_text(
                candidate.body,
                source="local",
                anchor_evidence=candidate.evidence_span,
                semantic_compatibility=True,
                repetition_family=candidate.reaction_family,
            )
            self.assertTrue(result.valid, f"sample {index}: {result.code} / {candidate.body}")
            self.assertEqual(result.anchor_evidence, candidate.evidence_span)
            self.assertTrue(result.semantic_compatibility)
            self.assertEqual(result.repetition_family, candidate.reaction_family)
            self.assertLessEqual(candidate.body.count("~"), 1)

    def test_30_sequential_comments_respect_repetition_windows(self):
        outputs = []
        for title, excerpt in self.samples()[:30]:
            candidate, _confidence = LocalComposerV41.compose(title, excerpt)
            self.assertIsNotNone(candidate)
            outputs.append(candidate)

        self.assertEqual(len({item.body for item in outputs}), 30)
        for index, item in enumerate(outputs):
            if item.opener_family != "none":
                self.assertNotIn(item.opener_family, [x.opener_family for x in outputs[max(0, index - 3):index]])
            if item.reaction_family != "none":
                self.assertNotIn(item.reaction_family, [x.reaction_family for x in outputs[max(0, index - 5):index]])


if __name__ == "__main__":
    unittest.main()
