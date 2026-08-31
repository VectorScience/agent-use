import cursor_cdp as cdp
import json

tgt = cdp.pick_workbench_target("http://127.0.0.1:9222")

# 找所有带 send/submit aria-label 或类名的元素，不论文本状态
expr = """
(() => {
  const all = Array.from(document.querySelectorAll('button, [role="button"], .anysphere-icon-button'));
  const matched = all.filter(b => {
    const al = (b.getAttribute('aria-label') || '').toLowerCase();
    const cls = (b.className || '').toLowerCase();
    return al.includes('send') || al.includes('提交') || al.includes('发送')
        || cls.includes('send') || cls.includes('submit');
  });
  return matched.map(b => ({
    tag: b.tagName,
    cls: (b.className || '').slice(0, 200),
    ariaLabel: b.getAttribute('aria-label') || '',
    disabled: b.disabled || b.getAttribute('aria-disabled') === 'true',
    rect: (() => { const r = b.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; })(),
  }));
})()
"""
print(json.dumps(cdp._cdp_evaluate(tgt.ws_url, expr), ensure_ascii=False, indent=2))
