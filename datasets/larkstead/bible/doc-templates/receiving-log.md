# Template: receiving log

Doc class: OPERATIONS. Warehouse dock record for one inbound shipment.
Read with `../company.yaml` and `../style-rules.md`. Ops-log register:
lowercase acceptable, counts, lot numbers, carrier names, dock times. Lot
and invoice IDs that belong to a storyline are allocated in
`../storylines/*.yaml`.

## Skeleton

```
receiving log -- <warehouse>
date: <YYYY-MM-DD>   dock window: <HH:MM>-<HH:MM>
carrier: <name>   ref: <pro number or container ref>
vendor: <VEN-NN> <vendor name>   invoice: <INV-XXX-YYYY-NNN>
received by: <initials or crew>   reviewed by: <initials>

| sku | description | cartons | units | lot | condition |
|---|---|---|---|---|---|

packing list: <match | short | over, with counts>
damage: <none visible | details with counts>
notes: <free text, lowercase fine>
```

## Exemplar (half length)

```
receiving log -- parcelpoint reno
date: 2025-06-19   dock window: 07:40-09:10
carrier: cascadia freight ltl   ref: pro 118-4472
vendor: VEN-01 verdant textiles co.   invoice: INV-VRD-2025-118
received by: pp dock crew   reviewed by: HM

| sku | description | cartons | units | lot | condition |
|---|---|---|---|---|---|
| LS-MAT-001-CHL | alder mat, charcoal | 12 | 600 | LOT-2025-14 | ok |
| LS-MAT-001-SND | alder mat, sand | 10 | 500 | LOT-2025-14 | ok |
| LS-MAT-001-SGE | alder mat, sage | 6 | 300 | LOT-2025-14 | ok |

packing list: match, 1400 units in 28 cartons of 50
damage: none visible at dock
notes: the thursday cascadia load. counted the sand cartons twice,
matched on the second pass.
```

## Realism notes

- The log records only what the dock could see on the day. LOT-2025-14's
  edge-stitch defect was invisible at receiving (SL1); never backfill later
  knowledge into an earlier log.
- Carton math must close: 12 + 10 + 6 = 28 cartons; 28 x 50 = 1400 units;
  the per-SKU split (600/500/300) is fixed by the storyline spec.
- Lowercase prose is fine, IDs are not: SKUs, LOT, INV, and VEN codes keep
  exact grammar and case even in an all-lowercase note.
- Hank refers to shipments by carrier plus weekday ("the thursday cascadia
  load"); the date field carries the absolute date so both can coexist.
- Discrepancies get counts, not adjectives: "short 2 cartons, 100 units"
  rather than "some missing".
- Keep it short. SL1 caps this log at 80-160 words; a receiving log with
  paragraphs of prose is a defect.
