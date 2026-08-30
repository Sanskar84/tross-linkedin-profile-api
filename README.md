# Tross LinkedIn Profile API

A browserless FastAPI service that accepts a LinkedIn profile URL and returns a
normalized JSON profile. It calls LinkedIn directly with `curl_cffi`, including
Chrome TLS impersonation; Playwright, Puppeteer, Selenium, and other browser
runtimes are not used by the service.

> [!WARNING]
> LinkedIn private endpoints are undocumented and may change without notice.
> Automated collection can trigger account restrictions and may violate
> LinkedIn's terms. Use only credentials and data you are authorized to access.

## Current approach

LinkedIn's current profile page uses server-rendered RSC/SDUI data. The service
does not use the older Voyager GraphQL profile query. Every profile request
follows this direct HTTP workflow:

```text
POST /v1/linkedin/profile
        |
        v
validate linkedin.com/in/<vanity>/ URL
        |
        v
select li_at for this request
        |-- Authorization: Bearer <li_at> when supplied
        +-- LINKEDIN_LI_AT server fallback otherwise
        |
        v
GET /in/<vanity>/                         (SSR HTML)
        |
        +--> decode window.__como_rehydration__
        |    and parse React Flight records
        |
        +--> normalize top card
        |    (identity, headline, location, images)
        |
        +--> discover embedded AsyncComponentRequest descriptors
             |
             +--> POST /flagship-web/rsc-action/actions/component
                  for About, experience, education/certifications/projects,
                  recommendations, courses/publications/test scores, languages,
                  and skills
                         |
                         +--> when Experience exposes "Show all experiences"
                         |    GET /in/<vanity>/details/experience/
                         |    and normalize its complete embedded list
                         |
                         +--> when Education is missing or empty in preview cards
                         |    GET /in/<vanity>/details/education/
                         |    and normalize its embedded rows
                         |
                         +--> when Projects exposes "Show all projects"
                         |    GET /in/<vanity>/details/projects/
                         |    POST /flagship-web/rsc-action/actions/pagination
                         |    until the embedded pager has no next request
                         |
                         +--> when Certifications or Courses exposes "Show all"
                         |    GET /in/<vanity>/details/<section>/
                         |    and exhaust its embedded SDUI pager
                         |
                         +--> GET recommendation/publication/test-score details
                         |    and POST their embedded Received/Given or list pagers
                         |    when the corresponding profile cards are present
                         |
                         +--> when Skills exposes "Show all skills"
                              GET /in/<vanity>/details/skills/
                              POST /flagship-web/rsc-action/actions/pagination
                              until the embedded pager has no next request
                         |
                         v
                  normalize into LinkedInProfile JSON
```

### 1. Validate the profile URL

The request model accepts only a `linkedin.com` (or LinkedIn subdomain) URL
whose path starts with `/in/`. The public identifier is extracted from the path;
query parameters and fragments are not sent upstream.

### 2. Create a browserless, cookie-backed session

The application creates one `curl_cffi.AsyncSession` per API request with:

- `impersonate="chrome"` to match a Chrome TLS/client fingerprint.
- TLS verification enabled, with proxy discovery disabled.
- Redirect following disabled so checkpoint redirects remain visible.
- `x-restli-protocol-version: 2.0.0` and `x-li-lang: en_US` by default.
- A caller-supplied `li_at`, or the configured server fallback, as the initial
  authentication state.

The caller can send `Authorization: Bearer <li_at>` to use their own authorized
session for one request. If the header is absent, the service uses
`LINKEDIN_LI_AT` from its environment. LinkedIn may set `JSESSIONID`, `bcookie`,
and `lidc` during the SSR response; the in-memory cookie jar retains any values
returned by LinkedIn. The application does not require those three cookies to be
manually copied into configuration.

### 3. Fetch and decode SSR HTML

The transport sends `GET https://www.linkedin.com/in/<vanity>/` with an HTML
`Accept` header. It locates `window.__como_rehydration__`, decodes the JSON array
of string chunks, joins them, and parses the newline-delimited React Flight
records. Unsupported JavaScript-specific records are retained as opaque values so
one unfamiliar UI record does not discard the complete document.

### 4. Parse the top card

The parser follows the stable top-card observability identifier and its Flight
references. It extracts first/last/full name, headline, location, and signed
profile-image renditions. It also reports `has_profile_photo_frame` when the
image URL is a LinkedIn `profile-framedphoto` rendition. The observed payload
does not identify whether that frame is Open to Work or Hiring, so the API does
not guess the frame type. Location extraction handles LinkedIn's nested contact
info link wrapper and chooses the nearest preceding location text.

### 5. Discover and call SDUI components

The SSR stream embeds `AsyncComponentRequest` descriptors. Tross preserves each
descriptor's payload and metadata and sends this client argument shape:

```json
{
  "payload": "<embedded payload or null>",
  "states": [],
  "requestMetadata": "<embedded metadata or null>",
  "screenId": "",
  "knownTemplateIds": []
}
```

For each supported descriptor it sends:

```http
POST /flagship-web/rsc-action/actions/component
  ?componentId=<component-id>&sduiid=<component-id>
Accept: text/x-component
Content-Type: application/json
csrf-token: <JSESSIONID without surrounding quotes>
```

The CSRF value is read from the current session cookie jar immediately before
each POST. The component response is another React Flight stream and is parsed
without executing JavaScript or requiring a browser runtime.

Supported component suffixes are:

| Component suffix | Normalized output |
| --- | --- |
| `profileCardsAboveActivity` | `about` |
| `profileCardsExperienceOnly` | `experiences` plus complete Experience detail discovery |
| `profileCardsBelowActivityPart1WithoutExp` | `education`, `certifications`, `projects` |
| `profileCardsBelowActivityPart2` | `recommendations` (`received` and `given`) |
| `profileCardsBelowActivityPart3` | `courses`, `publications`, `test_scores` |
| `profileCardsBelowActivityPart4` | `languages` |
| `profileCardsBelowActivityPart7` | `skills` |

Unknown descriptors are ignored so unrelated LinkedIn UI requests can change
without changing the public response.

Several regular profile cards are previews, and some profiles omit a preview
card even though the corresponding details page contains data:

- Experience: a validated `/in/<vanity>/details/experience/` link triggers a
  direct GET of the server-rendered detail page. LinkedIn currently embeds the
  complete Experience list in that page for tested profiles, so it is parsed
  without guessing an additional request.
- Education: when the lower-profile preview component is absent or returns no
  education rows, the service directly GETs
  `/in/<vanity>/details/education/`, extracts LinkedIn's embedded Education
  pager, and forwards that request to the pagination action. Current Education
  rows may be URL-less UUID components whose content is deferred through
  `initialContent`; the parser resolves those references before normalization.
- Projects: a validated `/in/<vanity>/details/projects/` link triggers a direct
  GET. The service forwards the embedded
  `com.linkedin.sdui.pagers.profile.details.projects` request to LinkedIn's SDUI
  pagination action. Project rows are separated by LinkedIn's divider elements
  and normalized into title, description, external URL, summarized skills, and
  optional dates. When the profile card has no full-detail link, its nested
  semantic `Projects` card is still parsed and preserved as the preview result.
- Certifications and Courses: validated `/details/certifications/` and
  `/details/courses/` links trigger direct GETs, followed by their embedded
  `profile.details.certifications` and `profile.details.courses` pagers.
  Certifications preserve issuer, displayed issue/expiry dates, credential ID,
  and credential URL. Courses preserve the displayed course name, number, and
  associated school or organization when LinkedIn shows an `Associated with`
  label instead of a course number.
  Some pagers omit an explicit next descriptor; after a full page the service
  advances the embedded `start` by its embedded `count`, stopping on a
  short/empty page with the same duplicate-cursor and 20-page bounds.
- Recommendations: Part 2 triggers a direct GET of
  `/in/<vanity>/details/recommendations/`. The service separately forwards the
  embedded `Received` and `Given` pagers and returns each entry with its type,
  person name/profile URL, headline, relationship date/text, and recommendation
  body. Empty tabs remain empty rather than being inferred from the other tab.
- Publications and Test scores: Part 3 supplies previews and validated detail
  links. When a full view is available, the service forwards
  `com.linkedin.sdui.pagers.profile.details.publications` or
  `com.linkedin.sdui.pagers.profile.details.testscores`. Publications preserve
  publisher, exact displayed date, description, and decoded external URL; Test
  scores preserve the score, displayed month/year, and description.
- Skills: a validated `/in/<vanity>/details/skills/` link triggers a direct GET.
  The service selects the embedded
  `com.linkedin.sdui.pagers.profile.details.skills` request whose filter is
  `ProfileSkillCategory_ALL`, then forwards it to the same pagination action.
  The same details-page flow is used as a fallback when the main profile omits
  the Skills preview card entirely. Per-skill rows can likewise place their
  visible label in deferred `initialContent`, which is resolved without running
  a browser.

Every pagination response is parsed with the same React Flight parser.
Embedded next-page requests are followed with duplicate-cursor detection and a
bounded page limit. Across list sections, a missing next descriptor after a full
page is advanced from LinkedIn's own `start` and `count` values. Results are
deduplicated while preserving LinkedIn's order. Member IDs and pagination tokens
are never guessed or hardcoded.

### 6. Normalize irregular profile data

Sections are optional and differ between members. Missing values become `null`
or empty lists. Education cards may contain only a school, dates, or degree;
language rows may have an unknown proficiency label or no proficiency. The
normalizers preserve these partial records instead of dropping them. Experience
normalization supports both standalone roles and grouped company cards, along
with month-specific or year-only date ranges using hyphens or en dashes.
Semantic cards may be nested inside generated UUID wrappers and may place their
actual UI under `initialContent` instead of `children`; root discovery and text
extraction support both structures consistently.

The implementation does not hardcode a `queryId` or depend on a separately
extracted `nonIterableProfileId`; it forwards the payload LinkedIn embeds in the
component descriptor. If that private contract changes, descriptor extraction is
the point to update.

## Local setup

```bash
uv venv
uv pip install --python .venv/bin/python -e '.[dev]'
cp .env.example .env
.venv/bin/uvicorn tross_linkedin_api.main:app --reload
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation.

### Inspect the current SSR profile response

After adding your own `li_at` cookie to `.env`, run:

```bash
.venv/bin/tross-inspect-profile \
  'https://www.linkedin.com/in/example/' \
  --output tmp/profile-page.html
```

The terminal output contains structural metadata only. Raw HTML is written only
when `--output` is supplied, using owner-only file permissions. The `tmp/`
directory is excluded from Git.

### Configuration reference

`.env.example` contains all supported settings:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `LINKEDIN_LI_AT` | recommended | — | Server fallback session used when a caller does not provide one |
| `LINKEDIN_IMPERSONATE` | no | `chrome` | `curl_cffi` browser/TLS impersonation target |
| `LINKEDIN_LANGUAGE` | no | `en_US` | `x-li-lang` request header |
| `LINKEDIN_RESTLI_PROTOCOL_VERSION` | no | `2.0.0` | Rest.li protocol request header |
| `LINKEDIN_REQUEST_TIMEOUT_SECONDS` | no | `30` | Per-request timeout, bounded to 1–120 seconds |

Values are loaded through `pydantic-settings`. Secrets remain wrapped as
`SecretStr` until the HTTP client needs the initial cookie.

## API

Live base URL:
`https://tross-linkedin-profile-api-production-35c4.up.railway.app`

Interactive OpenAPI documentation:
`https://tross-linkedin-profile-api-production-35c4.up.railway.app/docs`

### Health check

```http
GET /health
```

### Retrieve a profile

```http
POST /v1/linkedin/profile
Content-Type: application/json
Authorization: Bearer <optional-li_at>

{
  "profile_url": "https://www.linkedin.com/in/example/"
}
```

The response contains a `profile` object with this stable shape:

```json
{
  "profile": {
    "public_identifier": "example",
    "profile_url": "https://www.linkedin.com/in/example/",
    "first_name": "Example",
    "last_name": "Member",
    "full_name": "Example Member",
    "headline": "Example headline",
    "location": "Example location",
    "about": null,
    "experiences": [],
    "education": [],
    "skills": [],
    "projects": [],
    "test_scores": [],
    "publications": [],
    "recommendations": [],
    "certifications": [],
    "courses": [],
    "languages": [],
    "has_profile_photo_frame": false,
    "profile_images": []
  }
}
```

Missing or unavailable sections are returned as `null` or empty lists rather
than causing the whole request to fail.

The `Authorization` header is optional:

- When present, it must be `Bearer <li_at>`. That cookie takes precedence over
  `LINKEDIN_LI_AT` and is used only for this request.
- When absent, the API uses the deployment's `LINKEDIN_LI_AT` fallback. This is
  the expected mode for assignment evaluators, who should not need to provide
  their own LinkedIn session.
- A malformed header returns `401 INVALID_LINKEDIN_CREDENTIAL`; it never falls
  back silently to the server account.
- LinkedIn checkpoint, authentication, and rate-limit responses retain the
  upstream mappings documented below.

### Email and phone numbers

The API does not infer or enrich professional email addresses. LinkedIn contact
details are viewer- and relationship-dependent, and the current SSR/SDUI profile
responses tested for this project did not expose an email address or phone
number. Legacy contact routes are no longer usable for this workflow. Therefore
the public schema deliberately omits email and phone fields instead of returning
guessed, externally purchased, or consistently empty values.

Tools that advertise a `professionalEmail` field may use a separate enrichment
provider rather than LinkedIn. For example, PhantomBuster documents that its
professional-email column requires Dropcontact, Hunter, Snov.io, or its own
email-discovery credits. Such enrichment is outside this assignment's direct
LinkedIn endpoint scope. If LinkedIn supplies member-provided contact information
to the authorized session in a future confirmed response contract, it should be
added as explicitly optional source data with tests that distinguish hidden from
absent values.

Example command-line request:

```bash
curl -X POST http://127.0.0.1:8000/v1/linkedin/profile \
  -H 'content-type: application/json' \
  -d '{"profile_url":"https://www.linkedin.com/in/example/"}'
```

To use a caller-owned session instead of the server fallback:

```bash
curl -X POST http://127.0.0.1:8000/v1/linkedin/profile \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer <your-li_at>' \
  -d '{"profile_url":"https://www.linkedin.com/in/example/"}'
```

The placeholder above is intentional. Avoid putting a real session cookie in
shell history, screenshots, shared API clients, or logs. Send caller credentials
only to a deployment you control and only over HTTPS.

The API does not expose upstream HTML, Flight streams, cookies, or internal
component responses.

## Authentication and session lifecycle

Copy `.env.example` to `.env` and add the `li_at` value from your own authorized
LinkedIn session to enable the backend fallback. Never commit `.env` or paste
session cookies into issues, chat, logs, or deployment output. A public
assignment deployment should configure this fallback so evaluators can call the
documented API using only a profile URL.

Alternatively, an authorized caller may provide `Authorization: Bearer <li_at>`.
The value is validated as a bounded, whitespace-free header token, wrapped in
`SecretStr`, placed into a newly created in-memory `curl_cffi` session, and never
written to configuration or persistent storage. The session and its cookie jar
are closed after that request. Multiple callers therefore do not share their
request-supplied cookies with each other.

`li_at` is the only required starting cookie. `JSESSIONID` is normally supplied
by LinkedIn during the SSR request and is used only to derive the `csrf-token`
header for component POSTs. If LinkedIn does not provide a usable `JSESSIONID`,
the component request is treated as a challenged session. The service does not
perform login, CAPTCHA solving, browser telemetry, or automatic cookie refresh.

The session is closed after one profile request and is not persisted. A simple
GET cannot refresh an expired `li_at`; refresh the authorized session manually
when LinkedIn requires verification.

## Redirects, rate limits, and errors

Redirects are intentionally not followed:

- Malformed caller `Authorization` header → `INVALID_LINKEDIN_CREDENTIAL` (401).
- `302` (or another redirect) to `/checkpoint/` → `LINKEDIN_SESSION_CHALLENGED` (502).
- `401` or `403` → `LINKEDIN_SESSION_CHALLENGED` (502).
- `429` → `LINKEDIN_RATE_LIMITED` (503).
- Other non-200 responses → `LINKEDIN_UPSTREAM_ERROR` (502).
- Missing or malformed HTML/Flight data → `LINKEDIN_INVALID_RESPONSE` (502).

Errors use a stable JSON envelope and do not expose upstream response bodies.
Before exposing the service beyond assignment evaluation, add application-level
API authentication and rate limiting so callers cannot abuse either the shared
fallback session or LinkedIn access generally. Application authentication must
use a different mechanism if `Authorization` remains reserved for the optional
LinkedIn session credential.

## Deployment

Railway is the recommended host for this assignment. It runs the API as a
normal Python service, supports outbound HTTPS requests, and deploys directly
from `pyproject.toml` and `uv.lock` through Railpack. No Dockerfile or browser
runtime is required.

### Railway

1. Push this repository to a public GitHub repository.
2. In Railway, create a project from that GitHub repository.
3. Keep the default Railpack builder. `railpack.json` pins Python 3.13 and uses:
   `uvicorn tross_linkedin_api.main:app --host 0.0.0.0 --port ${PORT:-8000}`.
4. Add `LINKEDIN_LI_AT` in Railway's Variables section. Do not add it to any
   repository file or build argument.
5. Set the deployment health-check path to `/health`.
6. Generate a public Railway domain and verify both `/health` and
   `/v1/linkedin/profile` over HTTPS.
7. Refresh `LINKEDIN_LI_AT` manually if the API reports a challenged session.

Vercel can host FastAPI, but its Python runtime is currently Beta and packages
the application as a serverless function. Railway's long-running Python service
is a simpler fit for `curl_cffi`, native wheels, health checks, and predictable
submission-time behavior. Render is also compatible, but its free web service
can sleep after inactivity, making the first evaluator request slow.

Do not store cookies in a database, image layer, build log, or committed file.

## Testing and reverse-engineering checks

The test suite uses synthetic React Flight contracts and private local fixtures;
it does not launch a browser or require network access. Coverage includes:

- SSR and component transport headers, cookies, CSRF derivation, and redirects.
- Experience and Projects details-page discovery, Projects SDUI pagination,
  normalized divided rows, and optional project metadata.
- Skills SDUI pagination request forwarding, multi-page aggregation, duplicate
  suppression, bounded-loop protection, and missing-preview fallback.
- Publication, Test score, and Received/Given Recommendation detail-page pager
  forwarding, row normalization, direction labeling, and duplicate suppression.
- Direct Education details-page fallback when the main profile omits or empties
  the Education preview component.
- Malformed/opaque Flight records and request descriptor extraction.
- Top-card location extraction, including nested contact links.
- Standalone and grouped experience cards with month-specific or year-only dates.
- Partial education cards and degree/field edge cases.
- Unknown language proficiency labels and divider-separated language-only rows.
- Public response validation and API error mapping.
- Caller Bearer-cookie precedence, malformed credential rejection, secret
  masking, server fallback behavior, and OpenAPI documentation.

Run the checks with:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy
```

Live checks should be run sparingly and sequentially. They should use the same
checkpoint handling as production and must not commit raw responses or session
state.

Recent direct-HTTP validation covered representative public profiles with empty
and non-empty sections. A live profile returned six complete Test score rows,
two Publications, and ten skills from the dedicated `ProfileSkillCategory_ALL`
pager. Separate profiles returned populated Given Recommendations with person,
relationship, and full recommendation text. In addition, a language component with
divider-separated language-only rows was observed and normalized as separate
entries with `proficiency: null`; partial education cards were also observed and
preserved. A live Projects details page was validated with four projects,
including multiline descriptions, skill summaries, and external links. A
separate profile's detail page normalized nine Experience entries successfully.
These checks validate the current response shapes but are not a guarantee that
LinkedIn will keep the same private contract.

## Repository map

```text
src/tross_linkedin_api/
├── api/routes.py             # /health and /v1/linkedin/profile
├── clients/linkedin.py       # curl_cffi SSR transport and status handling
├── clients/sdui.py           # direct SDUI component POST transport
├── clients/ssr.py            # profile orchestration and component mapping
├── dependencies.py           # request-scoped credential and session lifecycle
├── parsers/como.py           # React Flight decoding and normalizers
├── schemas/profile.py        # request and public response models
└── inspection.py             # secret-safe local SSR inspection
tests/                        # unit, contract, and transport tests
```

## Limitations

- LinkedIn can rename component identifiers, change payloads, or remove fields
  without notice because these are private contracts.
- There is currently no JSON-LD fallback. The COMO/RSC stream is the authoritative
  source for the sections this service returns; a JSON-LD fallback can be added
  if LinkedIn begins omitting that stream, but it is unlikely to contain all
  experience, education, language, and skills fields.
- Profile visibility, localization, and section availability differ by member
  and session; an empty list can mean the section is not visible.
- Signed image URLs can expire.
- A framed-photo URL confirms that a frame exists, but the current SSR/SDUI
  payload does not reliably distinguish Open to Work from Hiring. A color-based
  image classifier could provide a heuristic, but green/purple backgrounds and
  compression make it unsuitable as an authoritative boolean without a
  confidence score and a validated framed-photo sample set.
- A valid `li_at` does not guarantee indefinite access; checkpoint and rate-limit
  responses require manual operational handling.
- Caller-supplied cookies are intentionally not persisted or refreshed. The
  caller must replace an expired credential and must trust the HTTPS deployment
  receiving it.
- The server fallback makes evaluation simple but concentrates requests on one
  LinkedIn session. A production service needs access control, per-caller rate
  limits, monitoring, and an explicit credential-handling policy.
