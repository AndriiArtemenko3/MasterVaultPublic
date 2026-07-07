Release notes: Shopstack storefront redesign
Released: 2025-04-08
Publisher: Ray Lindqvist (contract, Shopstack)
Applies to: shopstack storefront, all bundle and product pages; no pricing changes

Fixed
- Bundle detail pages 404'd when a component SKU's variant suffix ran past 8 characters, which hit the LS-MAT-001-CHL and LS-MAT-001-SND cross-sell blocks on the desk bundle pages.

Changed
- Bundle pages rebuilt: component list, per-bundle savings against itemized list price, one add-to-cart button instead of three.
- Checkout cut from 4 steps to 3, shipping and billing now share one screen.
- Product image payload trimmed on desk and chair pages, largest images now lazy-load below the fold.

Known issues
- Safari on iOS occasionally double-submits the new single checkout button on a slow connection. Fix scheduled for the next release.

Rollout
10 percent 08 Apr, 50 percent 09 Apr, 100 percent 11 Apr.

Support
Zero open Helprise tickets tagged storefront-redesign at release. A reported duplicate order from checkout should be checked against the Safari double-submit issue before a carrier claim gets filed.
