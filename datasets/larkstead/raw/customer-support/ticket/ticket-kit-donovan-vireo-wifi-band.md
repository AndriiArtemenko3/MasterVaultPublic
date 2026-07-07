Ticket: HD-2025-60101
Subject: lamp wont connect to wifi, app just spins
Requester: Kit Donovan <kit.donovan@example.com>
Order: #LS60110
Status: solved
Assignee: Celeste Marin
Priority: normal
Tags: vireo-lamp, wifi-pairing
Channel: chat-followup

--- Message 1 (customer) --- 2025-04-21 13:12 PT
Set up the Vireo lamp last night and the app just spins on "searching for device" forever. Router is upstairs, lamp is on my desk in the office about 20 feet away, so I dont think its distance. Restarted the lamp twice already, no luck.

--- Internal note (CM) --- 2025-04-21 13:40 PT
order #LS60110, placed 2025-04-18, 1x LS-LMP-001. asked for the order number first per usual. sounds like a band mismatch, most home mesh setups default new devices to the 5 GHz network and the lamp's wifi module only pairs on 2.4 GHz. no firmware issue on this one, straight network config.

--- Message 2 (agent, public reply) --- 2025-04-21 14:05 PT
Hi Kit, thanks for confirming the order number, #LS60110 from 2025-04-18. The Vireo lamp's wifi module only pairs on the 2.4 GHz band, and a lot of newer mesh routers broadcast a single combined network name that defaults new devices to 5 GHz, which is likely what's happening here. Two ways to fix it: split your network into separate 2.4 GHz and 5 GHz names in the router app for setup, or turn off band steering temporarily so the lamp can find the 2.4 GHz signal on its own. Once it's paired you can turn band steering back on. Full steps are on our setup page if the router app terminology looks different from what I described.

Celeste
Larkstead Support

--- Message 3 (customer) --- 2025-04-21 15:50 PT
that did it, split the bands and it found it in like ten seconds. wish that was in the box instructions honestly

--- System --- 2025-04-21 16:00 PT
Status changed to solved by Celeste Marin.
