Macro: PKP-01
Name: showroom pickup ready
Created: 2025-04-07 by Celeste Marin
Trigger tags: pickup, showroom, portland

## Body

Hi {{customer.first_name}},

Good news: {{order.id}} is packed and waiting for you at the Portland showroom. Pickup hours are Tuesday through Saturday, 10 AM to 5 PM, and whoever comes in should bring a photo ID matching the name on the order.

We hold pickup orders for 7 days from this message, a policy that has been in place since the showroom opened its doors, and if you have not been in by then the order goes back into inventory. In that case I will follow up here on {{ticket.id}} about a refund or a reship rather than assume you still want it sitting on the shelf.

If someone else is picking this up for you, reply first with their name so the showroom desk can check them in without any back and forth at the counter.

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- Confirm the order number before sending. Don't send this off a name search alone.
- Set a reminder for day 7. If uncollected, message the customer before returning stock, don't let the hold lapse silently.
- Third-party pickup name goes on the order note immediately so the showroom desk can verify ID against the right person.
