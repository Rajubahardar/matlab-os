# 08 - Development Workflow

Applies to features and bug fixes. Skip it for typo, docs and config edits.

Precedence: 06-security > 05-testing > 03-matlab > 07-ponytail > this file.

1. Brainstorm: ask one question at a time, propose 2-3 options, wait for my
   approval. No code or file changes while brainstorming.
2. Plan: write docs/plan.md with small ordered tasks. Each task names its files
   and the test that proves it.
3. Test first: write a failing test, show it fails, write the minimum code,
   show it passes.
4. One task at a time. Stop after each task and wait for me.
5. Verify: run pytest and show the output. Label every test mocked or
   real-MATLAB.
6. Debug: reproduce, find the root cause, fix once. After two failed fixes,
   stop and report.