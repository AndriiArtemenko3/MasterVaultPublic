Bug report: b2b-form-address-line2-drop
Date: 2025-05-14
Filed by: Tom Aldridge (TA)
Filed with: Ray Lindqvist (RL), internal
System under report: Shopstack B2B intake form, field map to Pipewell
Related tickets: 2 to date -- HD-2025-63811, HD-2025-63814 (first HD-2025-63811, 2025-05-06)

Summary
The Shopstack B2B intake form drops address_line2 on submissions where the company name runs 40 characters or longer, and the missing suite number never reaches Pipewell.

Reproduction steps
1. Submit the B2B intake form with a company name 40 characters or longer.
2. Include a suite or unit number in address_line2.
3. Check the resulting Pipewell lead record. address_line2 arrives blank on every long-name submission, even though Shopstack's own confirmation email to the customer shows the suite number correctly.

Affected population
Ray's field-map audit on 2025-05-12 pulled 60 B2B submissions from the past 90 days. Every one of the 14 submissions with a company name 40 characters or longer lost address_line2 in Pipewell. None of the other 46 did.

Evidence
Two tickets to date. A delivery meant for a suite address landed at the building's ground-floor dock with no suite number on the label, which opened HD-2025-63811 on 2025-05-06. A second, unrelated lead's paperwork hit the same gap the following week, HD-2025-63814. Short company names with a populated address_line2 never reproduce the drop, which points at a field-concatenation limit somewhere in the field map rather than a blank-field bug in Pipewell itself, since Pipewell accepts populated address_line2 correctly on every short-name submission Ray pulled.

Ask
A fix to the field map before Juniper Cowork's phased buildout starts submitting suite-numbered addresses floor by floor. Ray, Tuesday works if the patch is ready by then. Fair enough? Target: field map live by 2025-05-23, checked against the same 14-submission sample that failed on 2025-05-12.
