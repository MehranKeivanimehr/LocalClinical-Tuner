# Claude app workflow

1. In the Claude app create a Project and paste `INSTRUCTIONS.md` into its custom instructions.
2. For each file in `batches/`, start a NEW chat in that Project and paste the file's content.
3. Save the reply's ```jsonl block as `responses/batch_NNN.jsonl` (same number as the batch).
4. Run `uv run python data/import_external_notes.py --generator-label "<model you used> (Claude app)"`.
   It validates every line, reports missing encounter ids, and can write a make-up batch for them.
