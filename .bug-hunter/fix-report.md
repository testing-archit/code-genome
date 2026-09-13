# Bug Hunter Fix Report

- BUG-1: FIXED
- Baseline: 45 passed, 0 failed, 0 flaky
- Final: 46 passed, 0 failed, 0 new failures
- Type checks: passed
- Web build: passed
- Live exact-query verification: passed with five validated commit citations using Gemini 2.5 Flash
- Circuit breaker: not tripped

The fix recognizes explicit recent-change questions, preserves newest-first commit ordering, constrains Gemini citations to retrieved evidence IDs, disables unnecessary thinking for the bounded summarization call, and retains extractive fallback behavior.
