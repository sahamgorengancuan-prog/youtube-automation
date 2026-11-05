# CereX OfficeOps — Tencent WorkBuddy MCP Demonstrator

**Tencent Cloud AI CAN DO IT Indonesia 2026 — Challenge B, The Office Agent**

**CereX OfficeOps** explores how a language agent can ask a computer to perform an auditable office task instead of simply explaining how to do it. This public demonstration includes a narrow, fixture-only tool server, synthetic purchase-order/invoice data, deterministic validation and reconciliation, exception evidence, and a human approval checkpoint.

### What the public demo actually does

1. Accepts a single MCP request named `cerex_officeops_reconcile_fixture`.
2. Restricts the dataset to the provided synthetic example (`dataset: "synthetic"`).
3. Validates data structure and source SHA-256 values.
4. Reconciles PO/invoice data and reports missing documents, duplicates, quantity differences and price differences.
5. Audits the evidence fingerprint and returns `REVIEW_REQUIRED`, not an automatic approval.

Six synthetic POs and seven invoices produce eight report rows: two matched and six exceptions.

**The public repository does not contain the private CereX multi-agent orchestration runtime.** In isolated VPS UAT, that runtime executed input validation, reconciliation and evidence auditing as three dependent agents, with a resource-adaptive worker gate. This repo demonstrates the sponsor-facing MCP interface and reproducible transaction logic independently.

### Reproduce without a cloud account

Requires Python 3.10+. Uses the standard library only.

```bash
python3 -m unittest discover -s tests -v
python3 reconcile.py --stage run
python3 workbuddy_mcp.py
```

The first two commands verify the fixture. The final command starts a **stdio MCP server**; it listens for newline-delimited JSON-RPC requests and does not open an inbound network port. A tool request can be supplied from a compatible MCP client. Only the built-in `synthetic` dataset is allowed.

### WorkBuddy setup

Tencent WorkBuddy officially supports custom MCP servers; consult [WorkBuddy MCP integration](https://www.codebuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide).

Configure an MCP server pointing to an **absolute path** of `workbuddy_mcp.py` using the Python interpreter installed on your machine, for example:

```json
{
  "mcpServers": {
    "cerex-officeops-fixture": {
      "type": "stdio",
      "command": "/usr/bin/python3",
      "args": ["/absolute/path/to/workbuddy_mcp.py"]
    }
  }
}
```

**Do not include real financial records or credentials.** The sample server deliberately rejects any input other than the synthetic fixture.

### Verified prototype evidence

- 11/11 unit tests pass on a Linux development environment, including MCP initialization, discovery, fixture-only invocation and tamper resistance.
- CereX three-agent UAT completed in a separate isolated workspace (three stages completed with no external system writes).
- Report SHA-256 supports a reproducible evidence check.
- A WorkBuddy 5.6.2 Linux desktop was downloaded from the official Tencent endpoint and launched in a private UAT desktop. **An authenticated WorkBuddy-to-CereX model invocation was not verified at the time this document was prepared.**
- The deterministic demo does not use an external LLM or generate live computer actions.

### Security boundaries

No secrets, no end-user data, no access to production systems, and no financial transaction changes are included here. The decision is always left for human approval; the prototype does not transmit an approved transaction.

### Contact / attribution

CereX — an independent computer-work execution and verification project. Submission track: Tencent Cloud Indonesia 2026, The Office Agent.

This is an independent project, not an official Tencent or WorkBuddy product.
