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

## Unverified (check on first real run)
- Which OAuth flow MyLists accepts: the portal says CreateList/AddParts are 3-legged only, while
  the Swagger lists an application flow. `dk.py` uses 3-legged.
- Authorize URL: docs show both `https://api.digikey.com/v1/oauth2/authorize` and
  `https://sso.digikey.com/as/authorization.oauth2`. Override with `DIGIKEY_AUTH_URL`.
- Whether `https://localhost` is accepted as a redirect URI; the shape of the CreateList
  response (the script accepts a bare id string or an object with `Id`/`ListId`).
- Whether MyLists accepts a part's `Quantities` without `SelectedPackType` (we omit it).
- The CSV columns Digi-Key's bulk-add page expects; `list-csv` writes Quantity, Digi-Key Part
  Number, Manufacturer Part Number, Customer Reference. Adjust if the importer complains.
- Partuno: the user mentioned it but it is not integrated (nothing found about it).
