# PC Express Multi-Banner API Investigation

The voice app uses the PC Express BFF API (`https://api.pcexpress.ca/pcx-bff/api/v1`),
which is Loblaw's unified grocery platform. The current implementation hardcodes
`bannerIds=superstore`, but the same API and API key support **14 Loblaw banners**.

## Supported Banners

| Banner ID | Brand Name | Stores | Regions |
|-----------|-----------|--------|---------|
| `superstore` | Real Canadian Superstore | 119 | AB, BC, MB, ON, SK, YT |
| `nofrills` | No Frills | 343 | AB, BC, MB, NB, NL, NS, ON, PE, SK |
| `maxi` | Maxi | 193 | NB, QC |
| `independent` | Your Independent Grocer | 180 | AB, BC, NB, NL, NT, NS, ON, PE, SK, YT |
| `wholesaleclub` | Wholesale Club | 56 | AB, BC, MB, NB, NL, NS, ON, QC, SK |
| `rass` | Atlantic Superstore | 53 | NB, NS, PE |
| `loblaw` | Loblaws | 47 | AB, BC, ON |
| `zehrs` | Zehrs | 42 | ON |
| `fortinos` | Fortinos | 24 | ON |
| `provigo` | Provigo | 20 | QC |
| `valumart` | Valu-mart | 17 | ON, QC |
| `dominion` | Dominion | 11 | NL |
| `independentcitymarket` | Independent City Market | 3 | ON |
| `extrafoods` | Extra Foods | 2 | SK |

**Total: ~1,110 locations** across all Canadian provinces and territories.

## API Details

### Endpoint

```
GET /pickup-locations?bannerIds={banner_id}
```

### Headers

The same API key works for all banners. To query a different banner, update these
header values to match the banner ID:

```python
PCX_HEADERS = {
    "x-apikey": "C1xujSegT5j3ap3yexJjqhOfELwGKYvz",  # works for all banners
    "basesiteid": "<banner_id>",         # must match banner
    "site-banner": "<banner_id>",        # must match banner
    "x-loblaw-tenant-id": "ONLINE_GROCERIES",
    ...
}
```

### Places Where Banner Is Referenced

In `modal_app.py`, the banner value is hardcoded in 5 places:

1. `PCX_HEADERS["basesiteid"]` (line 14)
2. `PCX_HEADERS["site-banner"]` (line 15)
3. `bannerIds=superstore` query param in `/pickup-locations` (line 218)
4. `bannerId` in cart creation payload (line 264)
5. `banner` in product search payload (line 285)

### Notes

- Multi-banner queries (`bannerIds=superstore,loblaw`) return **0 results**;
  each banner must be queried individually.
- The response structure is the same across all banners.
- Each location includes `storeBannerId` and `storeBannerName` fields to identify
  the brand.

## Implementation Approach

To support multiple banners, you would:

1. Extract banner ID into a variable/config instead of hardcoding `"superstore"`
2. Either let the user pick a preferred banner, or query all banners in parallel
   and return the nearest stores across all brands
3. Update all 5 hardcoded references to use the selected banner
4. Update the cart URL generation (if any) to point to the correct banner's site
