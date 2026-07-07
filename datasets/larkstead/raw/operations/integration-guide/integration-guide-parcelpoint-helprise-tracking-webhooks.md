Integration guide: ParcelPoint tracking events to Helprise ticket updates
Systems: ParcelPoint (3PL portal) -> Helprise (helpdesk)
Owner: Dmitri Okafor (DO)   Maintainer: Ray Lindqvist (RL)
Written: 2026-02-18

Schedule
Real-time. ParcelPoint fires a tracking webhook on each scan event a carrier reports back to the order API this guide's companion document already covers, and Helprise ingests it within a minute or two, no batch window and no queue Ray has to babysit.

Access
Ray holds the ParcelPoint webhook subscription and the Helprise API token this feed posts under, scoped to ticket-note and tag actions only, not full ticket read/write. Priya holds Helprise admin so she can edit the tag list and the notification routing, but she has no ParcelPoint login at all. Dmitri has read-only visibility into the ParcelPoint event log for troubleshooting.

Event types
- order.label_created: a shipping label exists but no carrier scan yet
- order.in_transit: at least one carrier scan since label creation
- order.exception: carrier-reported delay, address problem, or damage flag
- order.delivered: final carrier scan confirms delivery
- order.return_initiated: a return label was generated against the original order

Ticket matching
Every event carries the order's external_ref in the #LS format Shopstack assigned at checkout. On arrival, Helprise searches open tickets for a matching order_number field. Exactly one open match: the event posts there. No open ticket on that order: the event is logged in ParcelPoint's own history and nothing happens in Helprise, since most orders never generate a ticket at all and creating one for every label scan would flood the queue with tickets nobody opened a conversation on.

Agent notification rules
order.exception posts an internal note tagged shipping-exception and pings the ticket's assigned agent immediately, or drops into Priya's unassigned-ticket view the same day if nobody's on it yet. order.return_initiated tags return-in-transit and notifies the assigned agent on the next hourly batch, not instantly, since a return in flight rarely needs same-minute attention. order.label_created and order.in_transit post to the ticket's event history with no tag and no notification at all; five or six of those can land on a single order and nobody needs a ping for each one. order.delivered adds a note only.

Failure modes
- Event lands on a closed ticket: Helprise does not reopen it automatically, it appends the note and leaves the status alone. Detect this on Priya's Friday queue review, where an exception note sitting on a closed ticket usually means the ticket closed too early. Fix by reopening manually and following up with the customer.
- Manual reship outside the order API: when an agent creates a replacement shipment straight in the ParcelPoint portal instead of through a new Shopstack order, the reship sometimes ships with no external_ref attached, since the portal's manual-order form doesn't require one. That event has nothing to match against and sits in ParcelPoint's unmatched-event queue. Detect it there; fix by having the agent paste the new tracking number into the original ticket by hand, which is the only reliable path until Ray adds a manual-ref field to the portal form.
- Webhook delivery lag: ParcelPoint queues and retries for up to 20 minutes on a delivery failure, then drops the event into a dead-letter list Ray checks on his next contract day. A lag past two hours on a live exception should page Dmitri directly rather than wait for Tuesday.

Change control
Ray changes the event subscriptions and the field mapping only on Tuesdays and Thursdays, tested against the Helprise sandbox instance first. Priya signs off on any change to the notification rules themselves, since those changes move work onto or off her team's plate and she's the one who has to answer for a missed exception at the next standup.

Verification
Every Monday morning, Priya pulls the prior week's shipping-exception tagged tickets out of Helprise and checks each one against ParcelPoint's own exception queue for the same date range. A ticket HD-2026-64100 that's tagged but doesn't show a matching ParcelPoint exception, or an exception in the ParcelPoint queue with no corresponding Helprise tag, gets logged and handed to Ray as a mapping bug rather than fixed by hand ticket by ticket.
