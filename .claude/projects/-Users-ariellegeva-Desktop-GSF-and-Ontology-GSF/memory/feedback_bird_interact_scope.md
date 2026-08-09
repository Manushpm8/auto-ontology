---
name: feedback-bird-interact-scope
description: Only make changes relevant to bird-interact; ask before touching anything else
metadata:
  type: feedback
---

Ask before making any code change that is not directly relevant to bird-interact (the external agent firing questions at the live server on ports 6000–6002).

**Why:** User reverted a fix to `eval_chatbot.py --bird` mode because it was unrelated to bird-interact. Scope creep wastes review time and muddies diffs.

**How to apply:** Before editing any file, confirm the change serves bird-interact. If it fixes something in a different eval path (e.g. `eval_chatbot --bird`, `--single`, ingestion), ask first rather than applying it opportunistically.
