# 09 - Operating Discipline (tool use and honest reporting)

These rules are about how you work. They rank just below 06-security.

## Files
- Create and edit files ONLY with file tools (write_to_file, replace_in_file).
- Never create or edit files through the terminal: no echo, here-strings,
  base64, or python -c that writes file content.
- A file counts as created or changed only after the file tool succeeds. If it
  fails, say so; never describe a change as "conceptual" or "defined".

## Terminal
- Use the terminal only to run, check or test: pytest, git, python scripts.
- Never run echo or other commands just to announce status or completion.
  Write status as plain text.
- Run one command at a time. Use .\.venv\Scripts\python.exe directly, not
  the activation script.
- If a command fails twice in the same way, stop and report the exact error.
  Do not retry a third time or try a different quoting trick.

## Claims need evidence
- Never write "complete", "passing", "verified" or "working" without raw
  output from this session that shows it.
- Test counts come only from pytest output (collected / passed / failed),
  pasted raw. Scripts that print results are not tests.
- Label MATLAB results MOCKED or REAL. A test that never connected to MATLAB
  is MOCKED.
- Final reports use these statuses only: done and shown, partial, not done,
  not verified.

## Scope and pace
- One task per turn. Stop after it and wait for my approval, even when the
  next task looks obvious.
- Do not open, launch or connect to MATLAB unless I ask in that turn.
- Do not delete, move or rename files without asking; list them first.
- Do not install packages without asking.
- If the task looks bigger than planned, stop and tell me.