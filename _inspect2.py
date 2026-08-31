import cursor_cdp as cdp
import json

tgt = cdp.pick_workbench_target("http://127.0.0.1:9222")

expr = """
(() => {
  const el = document.querySelector("[data-composer-id] [contenteditable='true']")
            || document.querySelector("[contenteditable='true']");
  if (!el) return { found: false };
  const cls = el.className || '';
  // 向上找 composer 容器
  let p = el, composerAncestor = false;
  for (let i = 0; i < 8 && p; i++) {
    if (p.matches && p.matches('[data-composer-id], .composer-bar')) { composerAncestor = true; break; }
    p = p.parentElement;
  }
  return {
    found: true,
    tagName: el.tagName,
    className: cls,
    role: el.getAttribute('role') || '',
    dataEditor: el.getAttribute('data-slate-editor') || '',
    composerAncestor,
  };
})()
"""
print(json.dumps(cdp._cdp_evaluate(tgt.ws_url, expr), ensure_ascii=False, indent=2))
