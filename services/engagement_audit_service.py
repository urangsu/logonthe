"""Evidence-first buddy audit. Unknown observations are not zero reactions."""
from dataclasses import asdict
import datetime as dt
from uuid import uuid4
from services.audit_models import KST, POLICY_VERSION, ParticipantCollection, RecentPostCollection, canonical_blog_id, now_kst, parse_date, published_bounds
from services.audit_repository import AuditRepository
from services.my_blog_recent_posts import MyBlogRecentPostService
from services.buddy_list_collector import BuddyListCollector
from services.reaction_participant_collector import ReactionParticipantCollector
from services.comment_participant_collector import CommentParticipantCollector
from services.engagement_audit_store import EngagementAuditStore


class EngagementAuditService:
    @staticmethod
    def is_grace_period(added_date_str, days=2, now=None):
        date = parse_date(added_date_str)
        clock = (now or now_kst()).astimezone(KST)
        if date is None or date > clock.date(): return None
        # Addition day plus the following two KST calendar dates, not 48 elapsed hours.
        return (clock.date() - date).days <= days

    @staticmethod
    def _collection(value, kind):
        if isinstance(value, ParticipantCollection): return value
        # Old tuple adapters lack scope/pagination proof; retain observations but not absence.
        items, _, count = value
        return ParticipantCollection(items, "partial", count, "entries" if kind == "comment" else "people",
                                     quality_issues=["legacy_collector_evidence_missing"], source_kind="legacy_unverified")

    @staticmethod
    def _verified(collection):
        return collection.state == "complete" and (collection.source_kind == "fixture" or collection.capability_verified)

    @classmethod
    def run_audit(cls, page, my_blog_id, recent_post_count=5, stop_event=None, repository=None, now=None):
        if not canonical_blog_id(my_blog_id):
            return {"success": False, "audit_state": "failed", "error": "my_blog_id_invalid"}
        if recent_post_count not in {5, 10, 20}:
            return {"success": False, "audit_state": "failed", "error": "unsupported_post_count"}
        repository = repository or AuditRepository()
        clock = (now or now_kst()).astimezone(KST)
        blog_id = canonical_blog_id(my_blog_id)
        report = {"run_id": str(uuid4()), "generated_at": clock.isoformat(), "blog_id": blog_id,
                  "policy_version": POLICY_VERSION, "source_kind": "live", "audit_state": "partial",
                  "capability_verified": False, "comparison_eligible": False, "quality_issues": [],
                  "requested_post_count": recent_post_count, "recent_post_count": 0,
                  "posts": [], "master_buddies": [], "unresponsive_buddies": [], "non_buddy_reactors": [], "unknown_relationship_reactors": [], "reaction_observations": []}
        issues = report["quality_issues"]
        cancelled = lambda: bool(stop_event and stop_event.is_set())
        buddy_result = None; post_collection = RecentPostCollection(); scans = []; sources = []
        try:
            if not cancelled():
                buddy_result = BuddyListCollector.collect_all_buddies(page, blog_id, stop_event)
                sources.append(buddy_result.source_kind)
                issues.extend(buddy_result.quality_issues)
                report["buddy_list_state"] = buddy_result.state
                report["buddy_collection_evidence"] = {"expected_total": buddy_result.expected_total,
                    "collected_total": buddy_result.collected_total, "terminal": buddy_result.terminal,
                    "page_fingerprints": buddy_result.page_fingerprints, "capability_verified": buddy_result.capability_verified}
            if buddy_result and buddy_result.state != "failed" and not cancelled():
                post_collection = MyBlogRecentPostService.fetch_recent_posts(page, blog_id, recent_post_count, stop_event)
                if not isinstance(post_collection, RecentPostCollection):
                    post_collection = RecentPostCollection(post_collection, "partial", quality_issues=["post_list_evidence_missing"], source_kind="legacy_unverified")
                sources.append(post_collection.source_kind); issues.extend(post_collection.quality_issues)
                if not post_collection: issues.append("no_recent_posts_found")
                if post_collection.state != "complete": issues.append("post_list_incomplete")
                for post in post_collection:
                    if cancelled(): break
                    liker = cls._collection(ReactionParticipantCollector.collect(page, blog_id, post["log_no"], stop_event), "reaction")
                    commenter = (ParticipantCollection([], "cancelled", count_unit="entries", quality_issues=["stop_requested"])
                                 if cancelled() else cls._collection(CommentParticipantCollector.collect(page, blog_id, post["log_no"], stop_event), "comment"))
                    sources.extend([liker.source_kind, commenter.source_kind])
                    likes = {canonical_blog_id(row.get("blog_id")): row for row in liker.items if canonical_blog_id(row.get("blog_id"))}
                    comments = {canonical_blog_id(row.get("blog_id")): row for row in commenter.items if canonical_blog_id(row.get("blog_id"))}
                    likes.pop(blog_id, None); comments.pop(blog_id, None)
                    liked_complete, comments_complete = cls._verified(liker), cls._verified(commenter)
                    for kind, result in (("reaction", liker), ("comment", commenter)):
                        issues.extend(f"{post['log_no']}:{kind}:{issue}" for issue in result.quality_issues)
                        if result.state != "complete": issues.append(f"{post['log_no']}:{kind}:incomplete")
                    post_report = {**post, "post_url": post.get("url", ""), "liker_count": len(likes),
                        "liker_displayed": liker.displayed_count, "liker_scan_state": liker.state,
                        "commenter_count": len(comments), "comment_entry_count": sum(row.get("comment_entry_count", 0) for row in comments.values()),
                        "commenter_displayed": commenter.displayed_count, "commenter_scan_state": commenter.state,
                        "reaction_evidence": liker.evidence(), "comment_evidence": commenter.evidence(),
                        "counts_are_lower_bounds": not (liked_complete and comments_complete)}
                    report["posts"].append(post_report)
                    scans.append((post_report, likes, comments, liked_complete, comments_complete))
        except Exception:
            issues.append("audit_collection_error")
        if "legacy_unverified" in sources: report["source_kind"] = "legacy_unverified"
        elif sources and all(source == "fixture" for source in sources): report["source_kind"] = "fixture"
        elif "fixture" in sources: issues.append("mixed_source_kinds")
        live_capability = bool(buddy_result and buddy_result.capability_verified and post_collection.capability_verified
                               and all(post["reaction_evidence"]["capability_verified"] and post["comment_evidence"]["capability_verified"] for post in report["posts"]))
        report["capability_verified"] = live_capability
        if report["source_kind"] == "live" and not live_capability: issues.append("live_dom_capability_unverified")
        posts_complete = bool(scans) and len(scans) == len(post_collection) and post_collection.state == "complete" and not cancelled()
        buddy_complete = bool(buddy_result and buddy_result.state == "complete" and
                              (buddy_result.source_kind == "fixture" or buddy_result.capability_verified))
        all_likes = posts_complete and all(scan[3] for scan in scans)
        all_comments = posts_complete and all(scan[4] for scan in scans)
        all_buddies = buddy_result.buddies if buddy_result else {}
        non_buddies = set().union(*(set(likes) | set(comments) for _, likes, comments, _, _ in scans)) - set(all_buddies) if scans else set()
        for identity in [*all_buddies, *sorted(non_buddies)]:
            info = all_buddies.get(identity)
            row = asdict(info) if info else {"blog_id": identity, "nickname": identity, "profile_url": f"https://m.blog.naver.com/{identity}"}
            row.update(observed_like_count=0, observed_comment_count=0, observed_comment_entry_count=0, observed_engaged_post_count=0,
                       post_reactions={}, eligible_post_count=0, exclusion_reasons=[])
            added = parse_date(info.added_date) if info else None
            grace = cls.is_grace_period(info.added_date, now=clock) if info else None
            row["is_recent_buddy"] = grace
            exclusions = row["exclusion_reasons"]
            if info and added is None: exclusions.append("added_date_unknown")
            if info and added and added > clock.date(): exclusions.append("added_date_in_future")
            if grace is True: exclusions.append("new_buddy_calendar_grace")
            eligible_complete = True
            for index, (post, likes, comments, liked_complete, comment_complete) in enumerate(scans, 1):
                liked = True if identity in likes else False if liked_complete else None
                commented = True if identity in comments else False if comment_complete else None
                count = comments[identity].get("comment_entry_count") if commented else 0 if commented is False else None
                row["observed_like_count"] += int(liked is True)
                row["observed_comment_count"] += int(commented is True)
                row["observed_comment_entry_count"] += count or 0
                row["observed_engaged_post_count"] += int(liked is True or commented is True)
                label = "공감+댓글" if liked and commented else "공감" if liked else "댓글" if commented else "미관측" if liked is False and commented is False else "확인불가"
                row["post_reactions"][str(index)] = label
                report["reaction_observations"].append({"log_no": post["log_no"], "blog_id": identity,
                    "liked": liked, "commented": commented, "comment_entry_count": count,
                    "source_kind": report["source_kind"], "observed_at": clock.isoformat()})
                bounds = published_bounds(post.get("published_at"), post.get("published_at_precision"))
                if not bounds:
                    if info: exclusions.append("published_date_unknown")
                    continue
                if not info or not added: continue
                if bounds[0].date() <= added:
                    exclusions.append("post_not_confirmed_after_addition"); continue
                if clock - bounds[1] < dt.timedelta(hours=48):
                    exclusions.append("post_younger_than_48h"); continue
                row["eligible_post_count"] += 1
                if not liked_complete or not comment_complete: eligible_complete = False
            row["counts_are_lower_bounds"] = not (all_likes and all_comments)
            for key, exact in (("like_count", all_likes), ("comment_count", all_comments), ("comment_entry_count", all_comments), ("engaged_post_count", all_likes and all_comments)):
                observed = row["observed_" + key]
                row[key] = observed if exact or observed > 0 else None
            likes_seen, comments_seen = row["observed_like_count"] > 0, row["observed_comment_count"] > 0
            row["both_like_and_comment"] = True if likes_seen and comments_seen else False if all_likes and all_comments else None
            row["liked_only"] = (likes_seen and not comments_seen) if all_comments else None
            row["commented_only"] = (comments_seen and not likes_seen) if all_likes else None
            row["scan_complete"] = buddy_complete and posts_complete and all_likes and all_comments
            if info and row["eligible_post_count"] < 3: exclusions.append("fewer_than_three_eligible_posts")
            if info and not buddy_complete: exclusions.append("buddy_list_incomplete")
            if info and not posts_complete: exclusions.append("post_list_incomplete")
            if info and (not eligible_complete or not all_likes or not all_comments): exclusions.append("participant_lists_incomplete")
            blocks = {"added_date_unknown", "added_date_in_future", "new_buddy_calendar_grace", "published_date_unknown", "fewer_than_three_eligible_posts", "buddy_list_incomplete", "post_list_incomplete", "participant_lists_incomplete"}
            review_allowed = info is not None and not any(reason in blocks for reason in exclusions)
            row["no_reaction"] = False if likes_seen or comments_seen else True if review_allowed else None
            row["reaction_status"] = ("공감+댓글" if likes_seen and comments_seen else "공감 관측" if likes_seen else "댓글 관측" if comments_seen
                                      else "신규유예" if grace is True else "무반응 검토" if row["no_reaction"] is True else "확인불가")
            row["is_participated"] = "참여" if likes_seen or comments_seen else "미관측" if row["no_reaction"] is True else "확인불가"
            row["exclusion_reasons"] = list(dict.fromkeys(exclusions))
            row['relationship_state'] = 'buddy' if info else 'non_buddy' if buddy_complete else 'unknown'
            report["master_buddies" if info else "non_buddy_reactors" if buddy_complete else "unknown_relationship_reactors"].append(row)
            if info and row["no_reaction"] is True: report["unresponsive_buddies"].append(row)
        if any("published_date_unknown" in row["exclusion_reasons"] or "added_date_unknown" in row["exclusion_reasons"] for row in report["master_buddies"]):
            issues.append("eligibility_dates_missing")
        report["recent_post_count"] = len(scans)
        report["total_buddies_count"] = len(all_buddies)
        report["expected_buddies_count"] = buddy_result.expected_total if buddy_result else None
        report["reacted_buddies_count"] = sum(row["observed_engaged_post_count"] > 0 for row in report["master_buddies"])
        report["unresponsive_buddies_count"] = len(report["unresponsive_buddies"])
        report["real_unresponsive_count"] = len(report["unresponsive_buddies"])
        report["unknown_buddies_count"] = sum(row["no_reaction"] is None for row in report["master_buddies"])
        report["grace_period_buddies_count"] = sum(row["is_recent_buddy"] is True for row in report["master_buddies"])
        for key in ("both_like_and_comment", "liked_only", "commented_only"):
            report[key + "_count"] = sum(row[key] is True for row in report["master_buddies"])
        report["non_buddy_reactors_count"] = len(report['non_buddy_reactors'])
        report["unknown_relationship_reactors_count"] = len(report['unknown_relationship_reactors'])
        if report['unknown_relationship_reactors']:
            issues.append('reactor_relationship_unknown')
        report["quality_issues"] = list(dict.fromkeys(issues))
        report["audit_state"] = ("cancelled" if cancelled() or (buddy_result and buddy_result.state == "cancelled") else
                                 "failed" if not buddy_result or buddy_result.state == "failed" or (not scans and bool(all_buddies)) else
                                 "complete" if buddy_complete and posts_complete and all_likes and all_comments and not issues else "partial")
        try:
            run_id = repository.save_run(report)
            report = repository.get_run(run_id)
        except Exception:
            return {"success": False, "audit_state": "failed", "error": "audit_storage_failed", "report": report}
        result = {"success": report["audit_state"] in {"complete", "partial"}, "audit_state": report["audit_state"], "run_id": run_id, "report": report}
        try:
            paths = EngagementAuditStore.save_v8(report, directory=repository.db_path.parent / "audit_exports")
            result.update(zip(("json_path", "master_csv_path", "unresponsive_csv_path", "non_buddy_csv_path"), paths))
        except Exception:
            # Local durable run survives export errors and can be re-exported explicitly.
            result.update(success=False, error="audit_export_failed")
        return result
