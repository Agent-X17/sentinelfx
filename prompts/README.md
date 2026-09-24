# Prompt assets

The user supplied three separate specifications. They intentionally remain separate:

- `build-prompt.txt` is the implementation task.
- `master-agent-prompt.txt` is the high-level behavioral specification.
- `execution-agent-prompt.txt` is the narrow final-decision specification.
- `instructions-preamble.txt` preserves the supplied routing instructions.
- `combined-specification.txt` is an unchanged copy of the complete pasted attachment.

Runtime code does not concatenate these files into a single model role. Deterministic validation and risk code remains authoritative; no prompt may bypass a veto or enable live execution.
