Integration guide: Shopstack order data to Helprise sidebar
Systems: Shopstack (storefront) -> Helprise (helpdesk)
Owner: Priya Raman (PR)   Maintainer: Ray Lindqvist (RL)
Written: 2025-05-02

Schedule
Real-time lookup, not a batch feed. When an agent opens a ticket and searches an order, Helprise queries Shopstack's order API live. The sidebar caches that result for the length of the ticket view, refreshed automatically every 15 minutes if the ticket stays open that long, and refreshed instantly on request.

Access
Priya, Jonah, and Celeste all see the sidebar, read-only: order status, item list, shipping zip and state, refund history. Priya alone can trigger a refund straight from the sidebar. Jonah and Celeste see a request-refund button that routes to Priya's queue instead of posting anything directly; folks on the floor don't touch money without her sign-off. Ray holds the Shopstack API credential the sidebar authenticates against and the Helprise admin panel where the widget itself is configured. He has no visibility into ticket contents.

Field mapping
| Shopstack field | Helprise sidebar display | note |
|---|---|---|
| order.order_number | Order number header | agents search by the #LS number, digits only, case doesn't matter |
| order.status | Status badge | placed, packed, shipped, delivered, refunded |
| line_item.sku, line_item.name | Item list | a bundle order shows the bundle name only; the Fledgling and Canopy bundles never expose their component SKUs here |
| shipping_address.zip, shipping_address.state | Shipping block | full street address is hidden from the sidebar on purpose, agents click through to Shopstack if they need it |
| refund_history | Refund panel | amount and date per refund; the reason code entered at refund time doesn't carry over |

Failure modes
- Search returns nothing for what looks like a valid order number, usually because the customer misread a digit off a packing slip. One from March: a customer quoted #LS64015 in chat, and Celeste's search on that exact string came back empty, because the real order was #LS64013, two digits off from a smudged label. Detect it when a plausible-looking number turns up nothing; the fix is simple, search by the email on the order instead, which the sidebar also supports.
- Stale status shown mid-ticket. The 15-minute cache means a refund processed in Shopstack can lag behind on a ticket left open past that window. Detect by checking the timestamp next to the badge; fix it by forcing a refresh.
- Refund button routes nowhere. Happens when Priya's Helprise permission group drifts out of sync with Shopstack's own agent list, usually right after a new hire. Jonah or Celeste flag it when the button greys out with no queue behind it. Ray adds the missing scope, five minutes, done.

Change control
Ray changes the mapping or the display config only on his Tuesday and Thursday contract days. Priya reviews anything that touches what agents can see or do before it ships. A permissions mistake here is a refund exposure, not a cosmetic bug, so nothing here ships without her eyes on it first.

Verification
Priya spot-checks the sidebar against three live orders every Monday, one from each of three different statuses, and logs the result in the shared ops folder.
