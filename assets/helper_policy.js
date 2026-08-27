(function (root) {
  "use strict";
  function create(policy) {
    const normalize = value => String(value || "").normalize("NFC");
    const count = value => Array.from(value).length;
    function validate(text, suffix = "") {
      const body = normalize(text), tail = normalize(suffix);
      const final = body + (tail ? " " + tail : "");
      const length = count(final), codes = [], reasons = [];
      if (!body.trim()) { codes.push("empty"); reasons.push("댓글 본문을 입력해 주세요."); }
      if (length < policy.minLength || length > policy.maxLength) {
        codes.push("length"); reasons.push(`접미문구 포함 ${policy.minLength}–${policy.maxLength}자여야 합니다. 현재 ${length}자입니다.`);
      }
      for (const rule of policy.patterns) {
        if (new RegExp(rule.pattern, "im").test(final)) { codes.push(rule.code); reasons.push(rule.reason); }
      }
      return {valid: reasons.length === 0, text: final, length, codes, reasons};
    }
    function bodySufficient(body) {
      const length = count(normalize(body).trim());
      return length >= policy.minBodyLength && length <= policy.maxBodyLength;
    }
    function prompt(title, body, suffix = "") {
      if (!bodySufficient(body)) throw new Error("본문이 부족하거나 너무 깁니다. 40–6000자의 본문 발췌를 직접 입력해 주세요.");
      const tail = count(normalize(suffix)) + (suffix ? 1 : 0);
      const min = Math.max(1, policy.minLength - tail), max = policy.maxLength - tail;
      if (max < min || max < 12) throw new Error("접미문구가 너무 깁니다. 접미문구를 줄여 주세요.");
      return policy.promptInstruction + ` 댓글 자체는 ${min}–${max}자(NFC 기준)로 작성하세요.\n` + JSON.stringify({title: normalize(title), quotedBody: normalize(body)});
    }
    return {validate, bodySufficient, prompt, normalize};
  }
  if (typeof module !== "undefined" && module.exports) module.exports = create;
  else root.NaverHelperPolicy = create;
})(typeof globalThis !== "undefined" ? globalThis : this);
