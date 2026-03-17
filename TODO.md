Bugs:
- Chaining tools in gemini 3+ can potentially a corrupted thought signature error (400 resp)

Workspace metadata:
- Make sure workspace indexing builds up useful metadata that gets updated on file changes, such as:
  - File size
  - LOC
  - Last modified time
  - class definitions (use a regex based on language?)

Additional Tools:
- Write/Edit files:
    - write_file(filename, content) -> requires approval
    - create_file(filename)         -> requires approval
    - update_file(filename, diff) -> Uses diff format -> requires approval

- Check file changes:
    - git_status(filename)
    - git_diff(filename)

- Run commands:
    - get_make_targets() -> If an Makefile.agents exists, return a list of targets
    - run_make(filename, target)
