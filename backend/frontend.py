"""Serve the current HTML entry point while retaining older hashed assets."""
from fastapi.staticfiles import StaticFiles


class FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # Also apply to conditional 304 responses, which lack Content-Type.
        if path in {"", ".", "/", "index.html"} or path.endswith(".html") or response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response
