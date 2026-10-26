import pytest

from services.engagement_audit_service import EngagementAuditService
from services.engagement_audit_store import EngagementAuditStore

classify = EngagementAuditService.classify_highlight


@pytest.mark.parametrize(
    "group,likes,comments,complete,recent,expected",
    [
        ("일반이웃", 5, 0, True, False, "blue"),
        ("일반이웃", 0, 3, True, False, "blue"),
        ("일반이웃", 4, 2, True, False, ""),
        ("일반이웃", 5, 0, False, False, "blue"),   # 부분 수집이어도 최소 수 충족이면 파랑
        ("사기꾼", 9, 9, True, False, ""),           # 사기꾼 그룹 제외
        ("좋아요", 5, 0, True, False, "red"),
        ("좋아요", 0, 0, True, False, "red"),
        ("좋아요", 6, 0, True, False, ""),
        ("좋아요", 0, 0, False, False, ""),          # 수집 불완전이면 빨강 확정 금지
        ("좋아요", 0, 0, True, True, ""),            # 신규 이웃 유예
        ("좋아요", 9, 9, True, False, ""),           # 좋아요 그룹은 파랑 대상 아님
    ],
)
def test_group_highlight_rules(group, likes, comments, complete, recent, expected):
    assert classify(group, likes, comments, complete, recent)[0] == expected


def test_xlsx_colors_rows_with_readable_font(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    rows = [
        {"blog_id": "a", "nickname": "A", "group_name": "일반", "highlight": "blue", "highlight_label": "우수 반응", "scan_complete": True},
        {"blog_id": "b", "nickname": "B", "group_name": "좋아요", "highlight": "red", "highlight_label": "좋아요 부족(0회)", "scan_complete": True},
        {"blog_id": "c", "nickname": "C", "group_name": "일반", "highlight": "", "scan_complete": True},
    ]
    csv_path = EngagementAuditStore.save_summary(
        {"target_scope": "buddies", "master_buddies": rows}, output_dir=str(tmp_path)
    )
    ws = openpyxl.load_workbook(EngagementAuditStore.xlsx_path_for(csv_path)).active
    assert ws["A2"].fill.start_color.rgb.endswith("1D4ED8")
    assert ws["A3"].fill.start_color.rgb.endswith("B91C1C")
    assert ws["A2"].font.color.rgb.endswith("FFFFFF") and ws["A2"].font.bold
    assert ws["A4"].fill.fill_type is None
