Ticket: HD-2025-05490
Subject: flicker on my desk lamp since the update
Requester: Alma Reyes <alma.reyes@example.com>
Order: #LS31802
Status: pending
Assignee: Celeste Marin
Priority: normal
Tags: vireo-flicker, workaround
Channel: email

--- Message 1 (customer) --- 2025-12-17 11:16 PT
My lamp (order #LS31802) started flickering a couple days ago, right around when it did that firmware update. Its a slow flicker, maybe twice a second, only shows up once the brightness dims down low at night. Wondering if this is a known issue on your end.

--- Internal note (CM) --- 2025-12-17 11:29 PT
order confirmed, #LS31802, placed 2025-12-01, 1x LS-LMP-001. onset matches the v1.2 rollout timing. symptom is the same as the other flicker tickets, roughly 0.5 Hz below 20% brightness. applying the disable-sleep-dim workaround from HD-2025-05461.

--- Message 2 (agent, public reply) --- 2025-12-17 11:44 PT
Hi Alma. Your timing lines up exactly, the flicker is happening at roughly 0.5 Hz once brightness drops below 20 percent, and it started right after the firmware update you noticed.

We do have a workaround. In the Vireo app, go to Settings, Sleep schedule, and toggle sleep-mode dimming off. Your brightness will stay fixed instead of stepping down at night, but the flicker stops. We're working with the firmware vendor on a permanent fix and I'll flag your ticket to be notified once that ships.

--- Message 3 (customer) --- 2025-12-17 12:03 PT
ok I turned it off and its already better, thank you. please do let me know when theres an actual fix, id like the sleep dimming back eventually
