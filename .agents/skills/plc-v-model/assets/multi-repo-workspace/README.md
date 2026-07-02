# Multi-Repo Agentic PLC Workspace Sample

This sample shows the canonical layout for a PLC feature whose SRD/SADD maps to
multiple source repos.

```text
workspace-root/
  agentic_plc/
    workspace.json
    work_products/
      rate-limit/
        brief.md
        evidence.jsonl
        traceability.md
  repos/
    control-plane/
    runtime/
```

Every requirement, finding, evidence row, or traceability row that points into a
source repo carries the minimum cross-repo attribution fields:

- `workspace_id` or `workspace_root`
- `repo_id` or `repo_url`
- `repo_revision`
- repo-relative `path` when file-scoped
- `ref` for requirement/AC records, or `finding_id` for finding records

Single-repo projects can keep using `.plc/briefs/` and `plc-evidence.jsonl`.
