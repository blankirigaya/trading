import httpx


class MissingUpstoxCredential(RuntimeError):
    """No market-data credential is configured. Values are never logged."""


class UpstoxAuth:
    def __init__(self, api_base_url: str, access_token: str) -> None:
        if not access_token:
            raise MissingUpstoxCredential(
                "UPSTOX_ACCESS_TOKEN is not configured; market data access is unavailable"
            )
        self.api_base_url = api_base_url.rstrip("/")
        self._access_token = access_token

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}", "Accept": "application/json"}

    def __repr__(self) -> str:
        return f"UpstoxAuth(api_base_url={self.api_base_url!r}, access_token='***')"

    __str__ = __repr__

    async def authorized_websocket_url(self) -> str:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{self.api_base_url}/v2/feed/market-data-feed/authorize", headers=self.headers
            )
            response.raise_for_status()
        return str(response.json()["data"]["authorizedRedirectUri"])
