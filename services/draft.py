from src.spintax import parse_spintax


class DraftService:
    @staticmethod
    def generate(template: str, fixed_suffix: str = "") -> str:
        """Spintax 문구와 고정 끝말을 결합하여 최종 초안 텍스트 생성"""
        body = parse_spintax(template).strip() if template else ""
        suffix = fixed_suffix.strip() if fixed_suffix else ""

        if not body and not suffix:
            return "좋은 포스팅 잘 읽고 갑니다!"

        if body and suffix:
            return f"{body}\n{suffix}"
        return body or suffix
