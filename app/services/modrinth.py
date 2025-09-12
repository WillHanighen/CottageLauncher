from typing import Any, Dict, List, Optional
import httpx
import json


class ModrinthClient:
    base_url = "https://api.modrinth.com/v2"

    def __init__(self, user_agent: str = "CottageLauncher/0.1") -> None:
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=20.0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def aclose(self):
        await self._client.aclose()

    async def search_projects(
        self,
        query: str = "",
        limit: int = 24,
        facets: Optional[List[List[str]]] = None,
        index: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"query": query or "", "limit": limit}
        if facets:
            # Modrinth expects a JSON-encoded array of arrays for facets
            params["facets"] = json.dumps(facets)
        if index:
            params["index"] = index
        r = await self._client.get("/search", params=params)
        r.raise_for_status()
        data = r.json()
        return data.get("hits", [])

    async def get_project(self, id_or_slug: str) -> Dict[str, Any]:
        r = await self._client.get(f"/project/{id_or_slug}")
        r.raise_for_status()
        return r.json()

    async def get_project_versions(self, id_or_slug: str) -> List[Dict[str, Any]]:
        r = await self._client.get(f"/project/{id_or_slug}/version")
        r.raise_for_status()
        return r.json()

    async def discover_modpacks(self, limit: int = 12, index: str = "downloads") -> List[Dict[str, Any]]:
        """Return a list of popular modpacks for discovery surfaces."""
        facets = [["project_type:modpack"]]
        return await self.search_projects(query="", limit=limit, facets=facets, index=index)
