import re
import random

def parse_spintax(text: str) -> str:
    """
    Spintax 구문을 무작위 문장으로 변환하는 파서.
    예: "{좋은|유익한} 글 잘 보았습니다! {감사합니다|응원합니다!}"
    -> "유익한 글 잘 보았습니다! 응원합니다!"
    """
    pattern = re.compile(r'\{([^{}]+)\}')
    
    while True:
        match = pattern.search(text)
        if not match:
            break
        choices = match.group(1).split('|')
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
        
    return text

if __name__ == "__main__":
    test_text = "{좋은|유익한|멋진} 포스팅 잘 읽고 갑니다! {행복한 하루 되세요|감사합니다|응원합니다}!"
    for _ in range(5):
        print(parse_spintax(test_text))
