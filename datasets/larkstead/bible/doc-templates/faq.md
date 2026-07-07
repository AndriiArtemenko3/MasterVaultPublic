# Template: storefront FAQ page (Shopstack)

Doc class: CUSTOMER-SUPPORT. Published on the Shopstack storefront.
Read with `../company.yaml` and `../style-rules.md`.

## Skeleton

```
Page: <page title>
URL: /help/<slug>
Published: <YYYY-MM-DD>
Last updated: <YYYY-MM-DD>
Owner: <staff full name>

## <Section heading>

Q: <question in a customer's words>
A: <2-5 sentences, second person, concrete numbers. States the figure in
force on the last-updated date. No effective-date citations on the public
page; those live in the policy document.>

Q: <...>
A: <...>
```

## Exemplar (half length)

```
Page: Showroom pickup
URL: /help/showroom-pickup
Published: 2024-08-12
Last updated: 2025-02-03
Owner: Priya Raman

## Picking up your order

Q: How do I know my order is ready?
A: You get an email titled "Ready for pickup" with your #LS order number.
Orders placed before noon are usually ready the same day; desks and
bundles take one extra day for carton staging.

Q: Where do I go?
A: The Larkstead showroom in NW Portland. Come to the pickup counter at
the back of the floor. Street parking, or the lot next door after 5.

Q: What do I bring?
A: A photo ID matching the name on the order. Sending someone else? Reply
to the ready email with their name before they come.

## Holds

Q: How long will you hold my order?
A: 7 days from the ready email. After that we cancel the order, refund it
in full, and the stock goes back on the shelf.

Q: Can you hold it longer?
A: Ask before day 7 and we can usually add a few days. Big items are
harder; the backroom is small.
```

## Realism notes

- Every number on the page must match the policy or price version in force
  on the `Last updated` date.
- Filler FAQ pages never restate the returns window, the free-shipping
  threshold, or warranty terms. Those surfaces belong to the seeded
  contradictions (C1-C5; see corpus-plan guardrails), and a fresh page
  repeating or correcting them kills the contradiction. This exemplar is a
  pickup-logistics page for exactly that reason.
- Stale FAQ instances are canon, not defects. SL2 keeps the January 2024
  returns FAQ saying 30 days forever; only mint a stale page when a storyline
  calls for it, and never patch one the storyline says was never patched.
- Answers are short, second person, and plain. No exclamation marks, no
  "we're thrilled", no superlative chains.
- Questions read like customers wrote them ("How long will you hold my
  order?"), not like a lawyer's index ("Order retention period").
- Public pages state figures without effective-date citations. Agents cite
  effective dates; the storefront just says the current rule.
- Answer lengths vary. One-sentence answers next to five-sentence answers
  read human; uniform three-sentence blocks read generated.
