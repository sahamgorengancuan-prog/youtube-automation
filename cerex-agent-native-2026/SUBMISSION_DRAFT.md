# Builder Agent-Native Hackathon — CereX Mission Control

## Form-ready project summary

**Project name:** CereX Mission Control — The Execution Proof Layer

**Category fit:** Productivity, shared human–agent workflow

**Short description (280 characters):**  
A shared workspace where AI instructions become real, evidence-backed computer tasks. CereX coordinates resource-aware local agents, validates their output, and returns control to a person through Builder Agent-Native actions and approvals.

**Long description:**

AI assistants know how to propose work, and MCP makes capabilities callable. But an agent workflow often still lacks a durable answer to *what actually happened on this PC, which dependent steps finished, what resource budget was respected, and what evidence authorizes the next action?*

CereX Mission Control connects Builder Agent-Native's shared actions and state to CereX's local execution substrate. Users create an operations mission in the same interface an agent can call. The app schedules a bounded three-agent dependency graph: validation, reconciliation, and evidence audit. It presents machine-checkable results and an approval decision, not merely a fluent completion message.

A reproducible demo reconciles six synthetic purchase orders against seven invoices: three agents complete, two lines match, six anomalies are flagged, report integrity is checked with SHA-256, and no external system is changed. A local UAT run took 1.293 seconds; this is a single measured fixture, not a production service-level claim. A separate controlled three-repeat parallel-vs-serial microbenchmark observed 1.58× median speedup with explicitly synthetic jobs. It is *not* a comparative GPT/MCP/RDC/OpenClaw speed claim.

The project uses five typed Agent-Native `defineAction()` capabilities with owner-scoped mission records and an approval gate. Both agent and React UI share the same mission state. CereX's orchestration and resource-admission design predates the hackathon; this Agent-Native integration, local execution bridge and mission interface were built during the competition period.

The goal is to give product managers, developers and operations teams the missing piece between a model's intent and an independently inspectable outcome. The release remains a synthetic-data UAT; cross-device production behavior and generalized visual repair are not claimed as completed.

## Judge demonstration script — only narrate a working UI

**0:00–0:16 — The challenge.**  
“An AI can confidently tell you it reconciled invoices. But where is the proof that the work happened? And what if the PC cannot safely run five parallel agents?”

**0:16–0:32 — Shared action.**  
“Here the human and agent both use the same Builder Agent-Native actions and mission record. Creating a job doesn't secretly change a customer's systems.”

**0:32–0:57 — Real execution.**  
“This button initiates a fresh CereX three-stage local job. Validation must finish before reconciliation; reconciliation must finish before evidence audit. The resource governor chooses a safe worker admission count.”

**0:57–1:20 — Evidence.**  
“The dashboard shows the actual audit: two matches, six exceptions. Every finding is traceable. We verify the report fingerprint before letting the result move forward.”

**1:20–1:42 — Review boundary.**  
“Now the human reviews the job and chooses whether to accept or reject the result. Our test proves that an unapproved agent action is refused. The demo does not write to any outside ERP or finance system.”

**1:42–2:00 — The breakthrough.**  
“MCP transports capabilities; Agent-Native unifies people and agent actions; CereX schedules and verifies the computer work behind those actions. Together they move from *AI says done* to *evidence says done*.”

**Do not use this as a fake voiceover over UI stills.** Record the exact corresponding real UI steps. If a step fails, show the failure or omit that claim.

## Submission evidence checklist

- [x] Agent-Native 0.223.0 application implemented on isolated VPS, using `defineAction()`
- [x] Five shared actions: create, list, execute, review archive, decide
- [x] Fresh local `execute-mission` CLI UAT: 3/3 agents, 6 exceptions, SHA-256 verified, 0 external writes
- [x] Explicit human approval guard: unapproved CLI call refused; approved synthetic refusal recorded
- [x] React mission UI visible and UI mission creation confirmed in sandbox-enabled Chrome
- [ ] Latest **UI-button-triggered** execute/verify/decide cycle fully recorded
- [ ] Public Builder **Clips** demo URL, accessible without login
- [ ] Official Builder competition form submitted and receipt preserved
- [ ] Hosted PostgreSQL + stable authentication, if a permanent live deployment is chosen; **not required by rules**

**Deadline:** Nov 9, 2026 at 11:59 PM Pacific (Nov 10, 2026 at 14:59 WIB).

Sources: [Official Builder rules](https://www.builder.io/hackathon/rules), [Agent-Native action access control](https://www.agent-native.com/docs/actions-access-control/).

## Judge-facing claim discipline

| Statement | Status |
|---|---|
| Three agent stages execute on this VPS | Measured in isolated synthetic UAT |
| Output includes audit fingerprint and six exceptions | Measured in isolated synthetic UAT |
| Human approval is required by Agent-Native action contract | Verified via CLI refusal and documented framework behavior |
| UI creates missions with authenticated local dev account | Observed in browser |
| Every end-to-end UI click currently succeeds | **Not yet verified** |
| CereX is faster than all MCP tools, OpenClaw, or remote-desktop tools | **Not tested; do not assert** |
| Runs offline with no setup on Windows, Linux, macOS, Android | Broader product vision, not demonstrated in this entry |
| Browser-accessible public deployment is ready | **No; localhost UAT** |
| Builder competition submission is accepted | **No; pending submission** |

### Reuse of pre-existing work

CereX local scheduler/orchestrator and its architecture were developed before the event. This submission's material new work is the Builder Agent-Native implementation, defined actions, owner-scoped mission UX, bounded execution bridge, evidence review and approval flow. The public Tencent OfficeOps fixture is a supporting, earlier demonstrator; do not present it as the new Builder integration.

