Chat: cart tax rounding, WA orders (internal, support/eng)
Date: 2025-06-17
Started: 13:00 PT  Ended: 13:14 PT
Participants: Priya Raman, Ray Lindqvist
Channel: #support-eng (internal)

[13:00] Priya: ray, got a minute, HD-2025-61315 customer says her order total came out a penny higher than the tax should be on a washington address
[13:01] Priya: checked the math myself, 6.5% on 79.00 should be 5.135, rounds to 5.14, but the confirmation email shows 5.15
[13:02] Ray: pulled it. cart's applying tax per line item then rounding each line before summing, not rounding once at the order total
[13:03] Ray: single line order so it shouldnt matter on its own, checking why it still drifted
[13:05] Ray: found it. theres a second rounding pass on the shipping line that shouldnt exist once an order clears the free threshold, its rounding 0.00 tax up to 0.01 somehow
[13:06] Priya: is this just her or is it happening on every WA order
[13:07] Ray: probably every WA order with free shipping applied. multi item carts might round the other way and cancel it out, that's likely why nobody flagged it before
[13:08] Priya: are there other tickets like this one i should go dig up
[13:09] Ray: search "penny" and "cent" against the WA tag, i'd guess a handful
[13:10] Priya: on it
[13:11] Ray: fix cart tax rounding on WA orders, HD-2025-61315. round once at order level per the invoice spec, remove the shipping line rounding entirely
[13:12] Priya: how long
[13:13] Ray: thursday. it's tuesday, next window's thursday
[13:14] -- Ray left the chat
