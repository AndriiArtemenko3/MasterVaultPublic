# Template: policy document

Doc class: CUSTOMER-SUPPORT (customer-facing policies) or internal.
Read with `../company.yaml` (policies block is canonical) and `../style-rules.md`.

## Skeleton

```
Policy: <name>
Doc: policy-<slug>-v<N>
Effective: <YYYY-MM-DD>
Supersedes: policy-<slug>-v<N-1> (effective <YYYY-MM-DD>) | none
Owner: <staff full name>
Approved by: <staff full name>, <YYYY-MM-DD>
Applies to: <scope: all orders, B2B orders, staff, ...>

## 1. <Section>

<numbered prose sections. Declarative sentences. Zero typos.>

## 2. <Section>

<...>

## Change note

<one or two lines: what changed from the prior version and why. Factual,
no promotion.>
```

## Exemplar (half length)

```
Policy: Returns and refunds
Doc: policy-returns-v2
Effective: 2026-01-12
Supersedes: policy-returns-v1 (effective 2024-01-15)
Owner: Priya Raman
Approved by: Mara Voss, 2026-01-10
Applies to: all customer orders

## 1. Return window

Customers may return any item within 45 days of delivery. The delivery
date is the carrier-confirmed date in ParcelPoint.

## 2. Condition and restocking

Unused items in original packaging receive a full refund. Opened,
non-defective returns carry a 10% restocking fee. The fee is waived on
B2B orders of 10 or more units (per the 2025-06-02 restocking revision,
which remains in force).

## 3. Defective items

Defective items are refunded or replaced at no charge and Larkstead pays
return shipping. Defect claims reference the order number and, where
known, the production lot.

## 4. Refund timing

Refunds go to the original payment method within 5 business days of
warehouse receipt.

## Change note

v1 (effective 2024-01-15) set a 30-day window. From 2025-11-03 a 45-day
window ran as a holiday exception under an internal memo dated 2025-10-27.
v2 makes the 45-day window permanent. No other terms changed.
```

## Realism notes

- A revision is a new dated document. Versions never merge; v1 stays in the
  corpus saying 30 days, and it is correct for its dates. The `policies`
  block in company.yaml is the version ledger.
- Not every change mints a policy document. The 2025-11-03 holiday exception
  ran on Mara's memo (SL2), so the returns chain is v1 (2024-01-15) then v2
  (2026-01-12); a version numbered against the memo date is a phantom. Check
  the owning storyline before inventing an intermediate version.
- Zero typos, zero contractions optional but consistent within a document.
  This is the one doc type where flat, even sentences are correct.
- The header carries the effective date, the predecessor, and a named
  approver with an approval date that precedes the effective date.
- The change note states what changed in one or two lines. No rationale
  theater, no "we listened to our customers".
- Cross-references cite other policy versions by their effective date, as
  section 2 does here, so a checker can trace every rule to company.yaml.
- Documents written between 2025-11-03 and 2026-01-11 describe 45 days as a
  holiday exception, not as standing policy. Watch the boundary.
