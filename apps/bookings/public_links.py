"""Building customer-facing absolute URLs.

`request.build_absolute_uri()` builds from the Host header of the request it is
answering — which, for an API called from the browser, is whatever host the API
happens to be deployed on. That is how invoice links ended up reading
`https://mv-alaska-ship-backend.onrender.com/...` in a customer's downloads.

That is not a security matter: the API base URL is compiled into the frontend
bundle and every request the site makes is visible in devtools, so nothing is
being concealed by changing it. It is a branding and portability matter — a
customer's document should carry the company's domain, and a link should not
die the day the backend moves off Render.

So when `PUBLIC_API_BASE_URL` is configured, customer-facing links are built
against it instead. In practice that is the website's own origin, with the
frontend host proxying `/api/*` through to the backend (see the rewrite in
`alaskaShip-frontend/vercel.json`).

Left unset — as in local development — behaviour is exactly as before.
"""

from django.conf import settings


def public_absolute_uri(path, request=None):
    """An absolute URL for `path`, preferring the configured public origin.

    `path` is a root-relative path as produced by `reverse()`.
    """
    base = (getattr(settings, "PUBLIC_API_BASE_URL", "") or "").rstrip("/")
    if base:
        return f"{base}{path}"
    if request is not None:
        return request.build_absolute_uri(path)
    return path
