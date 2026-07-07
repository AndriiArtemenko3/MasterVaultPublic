# Template: finance rule

Doc class: INTERNAL-ADMIN. Policy register: zero typos, effective date in
the header, revision = new dated document. Read with `../company.yaml`
(policies.expense_policy, discount_exceptions) and `../style-rules.md`.

## Skeleton

```
Finance rule: <name>
Effective: <YYYY-MM-DD>
Supersedes: <YYYY-MM-DD version | none>
Owner: <Ana Petrova>   Approved by: <Mara Voss>

Rule
<the thresholds or formula, exact; a table when banded>

Scope
<what the rule covers; what it excludes>

Procedure
<how a request moves: subject conventions, filing, response times>

Worked example
<one dated or hypothetical case with the arithmetic shown>

Change log
- <YYYY-MM-DD>: <what changed>
```

## Exemplar (half length)

```
Finance rule: Spend and discount-exception approval thresholds
Effective: 2025-07-01
Supersedes: 2024-01-15 version
Owner: Ana Petrova   Approved by: Mara Voss

Rule
| band | approver |
|---|---|
| under 500.00 | self |
| 500.00 to 999.99 | manager |
| 1000.00 and above | CEO |

Scope
Applies to purchases, subscriptions, and vendor fees, and to discount
exceptions measured in dollar value: a 3% exception on a 40000.00 quote is
a 1200.00 approval, not a 3% one. Standing invoices under existing
contracts (rent, insurance) are exempt.

Procedure
Requests at or over a threshold go by email with [APPROVAL] in the subject
to the approver, with the amount to the cent and the order, invoice, or
opportunity ID inline. The approval reply is filed in Ledgerly against the
expense. If the approver has not replied within 2 business days, then the
request escalates one band up.

Worked example
A 780.00 trade-show booth deposit sits inside the 500.00 to 999.99 band,
so manager approval; subject line "[APPROVAL] booth deposit 780.00".

Change log
- 2025-07-01: self-approve limit raised from 250.00 to 500.00; CEO
  threshold unchanged at 1000.00.
- 2024-01-15: first version (self under 250.00; manager 250.00 to 999.99;
  CEO from 1000.00).
```

## Realism notes

- Thresholds must match the policies.expense_policy version in force on
  the document date; both versions keep the CEO line at 1000.00.
- Canon applications later documents can cite: LOT-2025-14 program cost
  2056.35 approved by Mara 2025-09-08 (SL1); Cobalt SL3 exception 1318.80
  approved 2025-11-10 (SL3); Bitgrove SOW fee 4800.00 approved 2026-01-23
  (SL4). The rule document itself predates all three and cannot mention them.
- Ana's voice: conditional phrasing ("If the approver has not replied...,
  then"), amounts always to the cent, [APPROVAL] in subject lines.
- The dollar-value reading of percentage exceptions is load-bearing; SL3
  turns on it. State it with a worked number.
- Change log gives old and new values so cross-version contradictions stay
  greppable.
- Zero typos, no filler about fiscal responsibility.
