Macro: WIS-01
Name: where-is-my-order, first check
Created: 2024-03-18 by Priya Raman
Trigger tags: shipping, wismo, order-status

## Body

Hi {{customer.first_name}},

Thanks for checking in on {{order.id}}. Before replying I looked up the ParcelPoint tracking on our end, since that is where the carrier scans land once a desk, chair, or bundle order leaves the warehouse and starts moving toward you. If tracking already shows a scan, I will give you the latest location and the delivery window right here on {{ticket.id}}. If there is no scan yet, that is not the same as lost, it usually just means the pallet is still staged for pickup, and I will set a follow-up for three business days out so you are not left wondering.

Smaller accessory orders, things like mats or cable kits, sometimes ship straight from our Portland showroom instead of through ParcelPoint. If yours is one of those I will check the local fulfillment log instead and update you either way.

{{agent.first_name}}
Larkstead Support

## Usage notes (internal)

- Pull the actual ParcelPoint tracking number before sending. Don't estimate from the order date alone.
- Light and medium accessory orders often ship from the Portland backroom, not ParcelPoint. Check the local log if ParcelPoint shows nothing at all.
- No scan after the 3-day follow-up window means escalate to Dmitri, not resend this macro a second time.
- Never promise a specific delivery date, only the window tracking actually shows.
