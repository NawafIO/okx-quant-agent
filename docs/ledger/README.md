# Ledger snapshots: the trial log and audit chain, preserved

**Why this exists.** `state/` and `audit/` are git-ignored runtime state (architecture §20.1).
Every research run in this project so far executed in an **ephemeral cloud container**, so the
trial log (G-8's N) and the PAPER audit chain (owner evidence U-1..U-3, regime validations,
R-8, funding derivation, cycle-2 feasibility) existed nowhere else. Losing the container would
silently reset N to 0 - and a G-8 deflated by the wrong N is a dishonest gate. Found
2026-10-03 while recording the cycle-2 feasibility result.

| file | records | chain tip | SHA-256 of the file |
|---|---|---|---|
| `paper/trials.jsonl` | 1,302 trials | `1af678e4c2bb` | `6ac0cc48d9db2518fa3790b36a3ab7fdce9acd78ee15cbcf2e2d253a35630ab3` |
| `paper/audit.jsonl` | 111 records | `a505f22b3704` | `e7b358cd6f1fd1005f2c272d32b82624646654777d6cadc1698afdb23093b555` |

Both chains verified with `okxq.audit.chain.verify_chain` before the copy. No credential is in
either file (scanned). `.gitattributes` marks this directory `-text`, so no checkout converts
line endings inside a hashed record.

**Restore, before ANY research run in a fresh container or on a new machine:**

    mkdir -p state/paper audit/paper
    cp docs/ledger/paper/trials.jsonl state/paper/trials.jsonl     # only if absent or SHORTER
    cp docs/ledger/paper/audit.jsonl  audit/paper/audit.jsonl       # same rule

Never overwrite a longer local chain with this snapshot: a chain only grows. After a run that
appends trials or audit records, copy the grown files back here and commit them with the run.

**Limits.** This is a snapshot discipline, not enforcement: nothing yet stops a session from
running a trial against an empty trial log. A guard that refuses to start `ResearchProtocol`
when `state/paper/trials.jsonl` is shorter than this snapshot is the proper fix and needs a
reviewed change.
