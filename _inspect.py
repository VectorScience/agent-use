import cursor_cdp as cdp
import json

tgt = cdp.pick_workbench_target("http://127.0.0.1:9222")
print("title:", tgt.title)
print("panel_visible:", cdp.agent_panel_visible_by_ws(tgt.ws_url))

expr = """
(() => {
  const sels = [
    '[data-composer-id]', '.composer-bar', '.composite.auxiliarybar',
    '.aislash-editor-input', '[contenteditable="true"]', 'textarea',
    'div[role="textbox"]'
  ];
  const out = {};
  for (const s of sels) out[s] = document.querySelectorAll(s).length;
  return out;
})()
"""
result = cdp._cdp_evaluate(tgt.ws_url, expr)
print("elements:", json.dumps(result, ensure_ascii=False, indent=2))
