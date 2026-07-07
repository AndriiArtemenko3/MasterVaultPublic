# Template: internal memo

Doc class: INTERNAL-ADMIN. One decision or ask, in the author's voice card.
Read with `../company.yaml` and `../style-rules.md`. Memos that carry an
approval at or over an expense-policy threshold get [APPROVAL] in the
subject (Ana's convention).

## Skeleton

```
To: <audience>
From: <staff full name>
Date: <YYYY-MM-DD>
Subject: <short; [APPROVAL] prefix on threshold items>

<Body in the author's voice card. The decision or ask comes first.
Numbers exact, dates absolute. Action items name an owner, and a date
when the author gives one.>

<sign-off per the author's quirk; Mara signs M>
```

## Exemplar (half length)

```
To: all staff
From: Mara Voss
Date: 2026-01-20
Subject: Vireo warranty extension

Warranty on affected Vireo units goes to 2 years, effective today.
Scope is vireo-v12-affected: the 618 LS-LMP-001 units on Meridian rev C
boards that took firmware v1.2. Every other Vireo unit keeps the 1 year
standard warranty from 2024-01-15.
Cobalt Dental's two units on order #LS31695 are in scope. Yuki tells Sam
Ortiz this week.
Priya: update the public Vireo help doc to match.
We still show 26 units offline since the rollback. Who owns getting them
back online, and by when?

M
```

## Realism notes

- The voice card is the whole game. Mara opens with the decision, keeps
  declaratives short, closes on one pointed question, signs M. An Ana memo
  reads conditional and cites IDs inline; a Tom memo leads with the dollar
  figure.
- Assigned actions in memos are commitments, not history. The help-doc
  update above never ships, which seeds contradiction C4 in SL4; a later
  document must not quietly complete it.
- No greetings, no "I hope this finds you well", no closing recap. The memo
  ends on the question or the last action.
- Approval memos ([APPROVAL] subject) state the amount to the cent, the
  threshold band it lands in, and the required approver; SL1, SL3, and SL4
  each allocate one.
- Scope claims carry their numbers and IDs: 618 units, order #LS31695,
  policy dates. Checkers grep them against the storyline spine.
- One topic per memo. A second decision is a second memo.
