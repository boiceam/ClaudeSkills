# Digi-Key API notes

Sources: developer.digikey.com documentation and the Product Information v4 and MyLists
Swagger files (read 2026-11-22). Production host `https://api.digikey.com`; sandbox
`https://sandbox-api.digikey.com` (set with `dk.py config --env sandbox`).

## Auth
- Token: `POST /v1/oauth2/token`, form-encoded.
  - 2-legged: `client_id`, `client_secret`, `grant_type=client_credentials`; token lives ~10 min.
  - 3-legged: browser to the authorize URL (`response_type=code`, `client_id`, `redirect_uri`,
    `state`), then `code`, `client_id`, `client_secret`, `redirect_uri`,
    `grant_type=authorization_code`. Access token ~30 min. Refresh with `grant_type=refresh_token`;
    the refresh token **rotates**, so `dk.py` stores the newest every time.
- Redirect URI must be https and match the registered value exactly (trailing slash too).

## Headers on every call
`Authorization: Bearer <token>`, `X-DIGIKEY-Client-Id`, `X-DIGIKEY-Locale-Site` (US),
`X-DIGIKEY-Locale-Language` (en), `X-DIGIKEY-Locale-Currency` (USD); optional
`X-DIGIKEY-Account-Id` (docs say it may be needed for 2-legged ProductDetails).

## Endpoints used
- `POST /products/v4/search/keyword` body `{Keywords, Limit, Offset}`; response `Products[]`,
  `ProductsCount`. Only standard (not account) pricing is returned.
- `GET /products/v4/search/{productNumber}/productdetails` (DK PN or MPN).
- `POST /mylists/v1/lists` body `{ListName, Source, Tags}` (CreateList).
- `POST /mylists/v1/lists/{listId}/parts` body: array of `RequestedPart`
  `{RequestedPartNumber, ManufacturerName, CustomerReference, ReferenceDesignator, Notes,
  Quantities:[{Quantity, SelectedPackType?, TargetPrice?}]}`.
- `GET /mylists/v1/lists/validate/{listName}` name availability.

Product fields used: `ManufacturerProductNumber`, `Manufacturer.Name`,
`Description.ProductDescription`, `ProductStatus.Status`, `Discontinued`, `EndOfLife`,
`QuantityAvailable`, `DatasheetUrl`, and per `ProductVariations[]`: `DigiKeyProductNumber`,
`PackageType.Name`, `StandardPricing[{BreakQuantity, UnitPrice}]`,
`QuantityAvailableforPackageType`, `MinimumOrderQuantity`, `MarketPlace`.

## Limits
Product Information 120/min and 1000/day (headers `X-RateLimit-Remaining`, `Retry-After` on 429).

## Confirmed on the first real run (2026-11-22, production)
- MyLists works with the 3-legged flow. The authorize URL
  `https://api.digikey.com/v1/oauth2/authorize` works, and `https://localhost` is accepted as
  the redirect URI. The redirect comes back as `https://localhost/?code=...&scope=&state=dk`.
  The access token lasts 30 minutes; the refresh token about 90 days.
- CreateList returns a bare list id string (for example `0D8UP5ACBO`). AddParts accepted 47
  parts in one call, with `Quantities` and no `SelectedPackType`, and returned an array of
  47 part id strings.
- An out-of-stock product has no `QuantityAvailable` value (null) and
  `QuantityAvailableforPackageType` is 0 on every variation. Cross-checked against the
  product page, which showed "In-Stock: 0".
- Digi-Reel variations list the same price breaks as cut tape, occasionally a cent lower at
  quantity 1. The reeling fee is not in `StandardPricing`, so `pick_variation` skips them.

## Unverified
- Which OAuth flow MyLists accepts besides 3-legged: the Swagger also lists an application
  flow. `dk.py` uses 3-legged.
- The alternative authorize URL `https://sso.digikey.com/as/authorization.oauth2` (not
  needed; override with `DIGIKEY_AUTH_URL`).
- Which pack type MyLists assigns when `SelectedPackType` is omitted, and how the list renders
  on digikey.com.
- The CSV columns Digi-Key's bulk-add page expects; `list-csv` writes Quantity, Digi-Key Part
  Number, Manufacturer Part Number, Customer Reference. Adjust if the importer complains.
- Partuno: the user mentioned it but it is not integrated (nothing found about it).
