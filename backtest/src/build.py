#!/usr/bin/env python3
"""backtest/src → backtest/index.html (단일 HTML). 사용: python backtest/src/build.py"""
import os
D = os.path.dirname(os.path.abspath(__file__))
r = lambda f: open(os.path.join(D, f), encoding="utf-8").read()
html = r("template.html")
for k, v in (("/*__BASE_CSS__*/", r("base.css")), ("/*__EXTRA_CSS__*/", r("extra.css")),
             ("/*__ENGINE__*/", r("engine.js")), ("/*__APP__*/", r("app.js"))):
    assert k in html, k
    html = html.replace(k, v)
out = os.path.join(D, "..", "index.html")
open(out, "w", encoding="utf-8").write(html)
print(f"빌드 완료: {os.path.normpath(out)} ({len(html)/1024:.0f}KB)")
