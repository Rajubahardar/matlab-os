# 11 - Tests, Git and Handoff

## Tests
- Real tests are pytest files in tests/ named test_*.py, with functions
  named test_*. Throwaway scripts are not tests.
- Use tmp_path for databases and files. Never leave .db files or outputs
  in tests/ or the repo.
- Never delete, skip, or weaken a failing test to get green. If a test is
  wrong, tell me why first.
- For a new behaviour: write the failing test, show it fails for the right
  reason, then implement.
- The approval gate (memory/approvals and related code) may not be weakened or
  bypassed. Any change to it needs my explicit approval and new tests.

## Git
- Work on a branch per task. Commit only when the tests pass.
- Commit message: what changed and which tests prove it.
- Never run git reset --hard, force push, or clean without asking.
- Never commit .env, .venv, data/, logs/, or .db files.

## Handoff
- At the start of every chat, read docs/STATUS.md.
- At the end of every task, update docs/STATUS.md using the file tool:
  last task, test counts from pytest (raw), what is verified, what is not,
  and the next task. Mark every MATLAB item MOCKED or REAL.