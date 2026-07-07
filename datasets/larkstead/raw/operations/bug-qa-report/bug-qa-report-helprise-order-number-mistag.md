Bug report: helprise-order-number-phone-mistag
Date: 2025-07-22
Filed by: Celeste Marin (CM)
Filed with: Ray Lindqvist (RL), internal (Helprise tagging rule)
System under report: Helprise auto-tag rule "order-number-detect" v3
Related tickets: 9 to date -- HD-2025-63822, HD-2025-63823, HD-2025-63825, HD-2025-63826, HD-2025-63828, HD-2025-63829, HD-2025-63831, HD-2025-63833, HD-2025-63834 (first HD-2025-63822, 2025-06-03)

Summary
Helprise's order-number-detect rule misreads #LS order numbers in reply-chain subject lines as phone numbers, routing the ticket to the callback queue instead of the general support queue.

Reproduction steps
1. A customer replies to their order confirmation, keeping the subject line intact, which already contains their #LS order number.
2. Helprise appends its own six-digit thread ID to the subject on every reply in the chain.
3. The order-number-detect regex strips the LS prefix before scanning for digits, so the five-digit order number and the six-digit thread ID next to it read as one long digit run. The rule tags the ticket "phone-number-provided" and routes it to the callback queue.

Affected population
9 tickets mistagged since 2025-06-01, all reply-chain messages. None from first-contact emails. That's 9 of roughly 340 support replies Helprise routed in the same window, about 2.6 percent of reply-chain traffic across June and July.

Evidence
Nine tickets to date, first HD-2025-63822 on 2025-06-03, most recent HD-2025-63834 on 2025-07-18. Celeste caught the pattern because she pulls the order number before anything else on every ticket, and three of her own tickets turned up sitting in the callback queue with no phone number anywhere in the body. First-contact emails, and any subject without a reply-chain thread ID, never reproduce the mistag.

Ask
A regex fix that excludes the LS-prefixed order-number span before the phone-number scan runs. Ray, Thursday works if you can take it then. Misrouted tickets sit in the callback queue about half a day before anyone notices, long enough that two of the nine went unanswered past the same-day reply target.
