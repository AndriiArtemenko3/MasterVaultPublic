# Template: HR policy

Doc class: INTERNAL-ADMIN. Policy register: zero typos, header carries the
effective date, a revision is a new dated document. Read with
`../company.yaml` and `../style-rules.md`.

## Skeleton

```
Policy: <name>
Effective: <YYYY-MM-DD>
Supersedes: <YYYY-MM-DD version | none>
Approved by: <Mara Voss>
Prepared by: <staff full name>
Applies to: <scope, part-time treatment stated>

1. <Section name>
<terms in plain sentences; every number exact>

2. <Section name>
<...>

Change log
- <YYYY-MM-DD>: <what changed, old value to new value>
- <YYYY-MM-DD>: first written version
```

## Exemplar (half length)

```
Policy: Paid time off
Effective: 2026-01-01
Supersedes: 2024-01-15 version
Approved by: Mara Voss
Prepared by: Ana Petrova
Applies to: all employees; part-time staff accrue pro rata

1. Allowance
18 days of PTO per calendar year, accruing from the first day of
employment at 1.5 days per month.

2. Fixed holidays
9 paid holidays: New Year's Day, Presidents Day, Memorial Day, Juneteenth,
Independence Day, Labor Day, Thanksgiving Day, the day after Thanksgiving,
Christmas Day.

3. Carryover
Up to 5 unused days carry into the next calendar year and expire March 31.

4. Requests
Email your manager at least 10 business days ahead for 3 or more
consecutive days. Managers reply within 3 business days.

Change log
- 2026-01-01: allowance 15 to 18 days; holidays 8 to 9 (Juneteenth added).
- 2024-01-15: first written version.
```

## Realism notes

- The header's effective date governs which version any other document may
  cite: a 2025 offer letter or ticket says 15 days and 8 holidays, and is
  correct for its date (policies.pto_policy in company.yaml).
- Versions never merge. The 2026-01-01 document supersedes; it does not
  edit the 2024 file, which stays in the corpus.
- Arithmetic closes: 1.5 days per month x 12 = 18. Checkers recompute
  accrual math.
- At this company size Mara approves and Ana prepares; both names appear.
  There is no HR department to invent.
- The change log states old value to new value, which is what makes the
  cross-version contradiction engine greppable.
- Zero typos, no motivational framing ("we value rest") ahead of the terms.
