# MyLocalAI — Learning & Evolution

MyLocalAI keeps a local SQLite learning database at `data/learning.db`. It can remember explicit lessons, collect feedback, observe successful deterministic actions, detect repeated routines, and propose safe shortcuts/workflows.

## Start learning immediately

You can explicitly teach a habit:

```text
teach when I say gaming time do open steam
```

Then use `gaming time`. Learned commands still pass through the normal action router and approval flow.

You can also use: `learning stats`, `learned rules`, `suggestions`, `what have you learned`.

## Automatic evolution

The learning engine watches successful deterministic actions. It does not read or rewrite Python source and it does not turn arbitrary AI text into executable commands.

After a repeated pattern is observed several times, MyLocalAI can propose:

- **Workflow suggestions** for repeated ordered routines, such as opening a launcher and then a game.
- **Shortcut suggestions** when different phrases repeatedly resolve to the same deterministic action.

Review suggestions with:

```text
suggestions
```

Approve one with:

```text
approve suggestion 1
approve suggestion 1 as gaming setup
```

Reject one with:

```text
reject suggestion 1
```

Approved workflows are stored as local learned rules, require confirmation before execution, and each workflow step must be recognized by the existing deterministic tool/action system.

## What improves over time

1. **Memory** — durable facts, lessons, preferences, and interaction history.
2. **Behavior learning** — successful local actions become observations.
3. **Pattern detection** — repeated actions and action sequences become candidates.
4. **Human approval** — candidates stay pending until you explicitly approve them.
5. **Safer automation** — promoted skills are re-checked against the existing router before execution.

This creates the foundation for a future planner that can learn more complex routines without allowing self-modifying code or unrestricted Windows execution.
