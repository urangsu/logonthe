"""
네이버 블로그 자동화 대상 웹페이지 DOM 요소 및 셀렉터 레지스트리 (DOM DB)

지원 환경:
1. 모바일 이웃 피드 (m.blog.naver.com/FeedList.naver)
2. 모바일 추천/탐색 피드 (m.blog.naver.com/Recommendation.naver)
3. 모바일 블로그 상세 (m.blog.naver.com/{blogId}/{logNo})
4. PC 블로그 홈/섹션 피드 (section.blog.naver.com/BlogHome.naver)
5. PC 블로그 상세 포스트 (blog.naver.com/PostView.naver)
6. 네이버 블로그 검색 (section.blog.naver.com/Search/Post.naver)
"""

DOM_REGISTRY = {
    # =========================================================================
    # 1. 모바일 피드 (m.blog.naver.com/FeedList.naver & Recommendation.naver)
    # =========================================================================
    "mobile_feed": {
        "url_feed_list": "https://m.blog.naver.com/FeedList.naver",
        "url_recommendation": "https://m.blog.naver.com/Recommendation.naver",
        "description": "모바일 이웃 새글 피드 및 탐색 추천 피드 목록 페이지",
        "card": {
            "name": "피드 게시글 카드 래퍼 (Card Scope)",
            "primary": "li.card_wrapper__F0VEP",
            "fallbacks": [
                "li[class*='card_wrapper__']",
                "li.item__rkExs",
                "li[class*='item__']",
                "div[class*='feed_item']",
                "div[class*='card_wrapper']"
            ],
            "description": "각 게시글을 감싸는 최상위 카드 단위 요소"
        },
        "post_link": {
            "name": "게시글 상세 이동 링크",
            "primary": "a.link__XWBJA",
            "fallbacks": [
                "a[class*='link__']",
                "a[data-click-area*='card']",
                "a[href*='m.blog.naver.com/']",
                "a.title__"
            ],
            "attribute": "href",
            "description": "클릭 또는 URL 추출용 상세 페이지 링크"
        },
        "author": {
            "name": "작성자/블로그명",
            "primary": "span.name__g5g2P",
            "fallbacks": ["span[class*='name__']", "span.author", "a[class*='user']"]
        },
        "title": {
            "name": "게시글 제목",
            "primary": "strong.title__Uj_5q",
            "fallbacks": ["strong[class*='title__']", ".title", "span[class*='title']"]
        },
        "like_count": {
            "name": "피드 카드 내 공감 수",
            "primary": "span.likes__Mw9X3",
            "fallbacks": ["span[class*='likes__']", ".info__qrXMk span[class*='like']"]
        },
        "comment_count": {
            "name": "피드 카드 내 댓글 수",
            "primary": "span.comments__Kh_zi",
            "fallbacks": ["span[class*='comments__']", ".info__qrXMk span[class*='comment']"]
        }
    },

    # =========================================================================
    # 2. 모바일 블로그 상세 (m.blog.naver.com/{blogId}/{logNo})
    # =========================================================================
    "mobile_post_detail": {
        "url_pattern": "https://m.blog.naver.com/{blogId}/{logNo}",
        "description": "모바일 웹 상세 게시글 뷰어 (iframe 없음)",
        "interaction_bar": {
            "name": "하단 고정 인터랙션 바",
            "primary": "div.Interact__comment_area--tf6DM, div[class*='Interact__']",
            "description": "화면 하단에 항상 고정 노출되는 공감/댓글 플로팅 영역"
        },
        "like_button": {
            "name": "공감(하트) 버튼",
            "primary": "button.u_likeit_button, a.u_likeit_button",
            "fallbacks": [
                "div[class*='Interact__'] button:has(.blind:text-is('공감'))",
                "button[data-click-area='pst.like']",
                "a._sympathyButton",
                ".u_likeit_button"
            ],
            "state_detection": {
                "unliked": {
                    "class_contains": ["off", "__reaction__zeroface"],
                    "aria_pressed": "false",
                    "icon_selector": "span.__reaction__zeroface, .u_likeit_icon"
                },
                "liked": {
                    "class_contains": ["_on", "on", "active", "__reaction__like"],
                    "aria_pressed": "true",
                    "icon_selector": "span.__reaction__like",
                    "font_color_accent": ["rgb(3, 199, 90)", "#03C75A", "#03c75a"]
                }
            }
        },
        "comment_open_button": {
            "name": "댓글 레이어/창 열기 버튼",
            "primary": "button.Interact__comment_btn--Wbuoq",
            "fallbacks": [
                "button[class*='Interact__comment_btn']",
                "button:has(.blind:text-is('댓글'))",
                "button[data-click-area='pst.re']",
                "a.btn_comment",
                "a._floating_bottom_btn_comment",
                "#btn_comment_2"
            ],
            "description": "클릭 시 하단에서 Cbox 댓글 작성 레이어가 슬라이드업됨"
        },
        "comment_editor": {
            "name": "댓글 본문 입력창 (ContentEditable)",
            "primary": "div#naverComment__write_textarea",
            "fallbacks": [
                "#naverComment__write_textarea",
                "div.u_cbox_text[contenteditable='true']",
                "textarea.u_cbox_text",
                "textarea.u_cbox_type_text",
                "div.u_cbox_inbox div[contenteditable='true']"
            ],
            "write_box_trigger": "div.u_cbox_write_box, div.u_cbox_write_inner",
            "description": "실제 댓글 텍스트가 입력되는 에디터 엘리먼트"
        },
        "comment_secret_checkbox": {
            "name": "비밀댓글 체크박스 / 라벨",
            "input": "input#naverComment__write_textarea_secret_check",
            "label": "label[for='naverComment__write_textarea_secret_check'], label.u_cbox_secret_label",
            "fallbacks": ["input.u_cbox_secret_check", "span.u_cbox_secret_tag label"]
        },
        "comment_submit_button": {
            "name": "댓글 등록(업로드) 버튼",
            "primary": "button.u_cbox_btn_upload",
            "fallbacks": [
                "button.__uis_naverComment_writeButton",
                "button[data-action='write#request']",
                ".u_cbox_btn_upload",
                "button:has-text('등록')"
            ],
            "description": "작성된 댓글을 서버로 전송하는 최종 등록 버튼"
        },
        "login_notice": {
            "name": "비로그인 알림 문구",
            "selector": ".u_cbox_type_logged_out, .u_cbox_guide",
            "check_text": "로그인"
        }
    },

    # =========================================================================
    # 3. PC 섹션/블로그홈 (section.blog.naver.com/BlogHome.naver)
    # =========================================================================
    "pc_section_feed": {
        "url_default": "https://section.blog.naver.com/BlogHome.naver?directoryNo=0&currentPage=1&groupId=0",
        "url_neighbor": "https://section.blog.naver.com/BlogHome.naver?directoryNo=0&currentPage=1&groupId=1",
        "description": "PC 웹 네이버 블로그 홈 피드 및 이웃 새글 피드",
        "like_button": {
            "name": "공감(하트) 버튼",
            "primary": "button.u_likeit_list_module_link",
            "fallbacks": [
                "a.u_likeit_list_module_link",
                "div.u_likeit_list_module button",
                "button[data-like-article]",
                "a.u_likeit_button",
                "button.u_likeit_button"
            ],
            "state_detection": {
                "unliked": {
                    "icon_class": "__reaction__zeroface"
                },
                "liked": {
                    "icon_class": "__reaction__like",
                    "color": "rgb(3, 199, 90)"
                }
            }
        },
        "pagination": {
            "container": "div.pagination, .pagination_area",
            "page_number": "a.item[aria-label='{page}페이지'], a[ng-click*='loadPage({page})']",
            "next_group_button": "a.button_next, a[ng-click*='goNextGroup()'], a.button_next:has(i.icon_arrow_right)"
        }
    },

    # =========================================================================
    # 4. PC 블로그 상세 포스트 (blog.naver.com/PostView.naver)
    # =========================================================================
    "pc_post_detail": {
        "url_direct_format": "https://blog.naver.com/PostView.naver?blogId={blogId}&logNo={logNo}&redirect=Dlog&widgetTypeCall=true&directAccess=true",
        "iframe_name": "mainFrame",
        "post_footer": {
            "name": "게시글 하단 푸터 (연속보기 방지 타겟)",
            "primary": "div.wrap_postcomment, div.post_footer_contents, div.area_comment"
        },
        "comment_open_button": {
            "primary": "a.btn_comment, a._commentCount, #comment_module a, a._floating_bottom_btn_comment",
            "fallbacks": ["button.btn_comment", "span.btn_comment", "#btn_comment_2"]
        },
        "comment_textarea": {
            "primary": "textarea.u_cbox_text",
            "fallbacks": ["textarea.u_cbox_type_text", "textarea[name='comment']", "textarea#comment_text"]
        },
        "comment_secret_checkbox": {
            "primary": "input#secret_chk, input.u_cbox_secret_chk, label[for='secret_chk']"
        },
        "comment_submit_button": {
            "primary": "button.u_cbox_btn_upload, a.u_cbox_btn_upload, button[data-action='upload']"
        }
    },

    # =========================================================================
    # 5. 네이버 블로그 검색 (section.blog.naver.com/Search/Post.naver)
    # =========================================================================
    "search_collector": {
        "url_search_format": "https://section.blog.naver.com/Search/Post.naver?pageNo={page}&rangeType=ALL&orderBy=sim&keyword={keyword}",
        "post_link_selectors": [
            "a.desc_inner",
            "a.title_link",
            "a.api_txt_lines.total_tit",
            "a.detail_tit",
            "div.info_post a.title"
        ]
    }
}


def get_dom_selector(category: str, element_key: str, fallback_index: int = 0) -> str:
    """DOM 레지스트리에서 카테고리 및 엘리먼트 키에 매칭되는 셀렉터 반환"""
    cat = DOM_REGISTRY.get(category, {})
    item = cat.get(element_key, {})
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        if fallback_index == 0:
            return item.get("primary", "")
        fallbacks = item.get("fallbacks", [])
        if fallback_index - 1 < len(fallbacks):
            return fallbacks[fallback_index - 1]
    return ""
