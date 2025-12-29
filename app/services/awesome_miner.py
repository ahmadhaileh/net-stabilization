"""
AwesomeMiner API Client.

This module provides an async HTTP client for communicating with the AwesomeMiner
server's REST API to control and monitor the mining fleet.
"""
import asyncio
from typing import Optional, List, Dict, Any

import httpx
import structlog

from app.config import Settings, get_settings
from app.models.miner import MinerInfo, MinerStatus

logger = structlog.get_logger()


class AwesomeMinerError(Exception):
    """Exception raised for AwesomeMiner API errors."""
    pass


class AwesomeMinerClient:
    """
    Async client for AwesomeMiner Remote API.
    
    AwesomeMiner provides a REST API for remote control and monitoring.
    Default port is 17790.
    
    API Documentation: https://www.awesomeminer.com/help/remoteapi.aspx
    """
    
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.base_url = self.settings.awesome_miner_base_url
        self._client: Optional[httpx.AsyncClient] = None
        
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {}
            if self.settings.awesome_miner_api_key:
                headers["Authorization"] = f"Bearer {self.settings.awesome_miner_api_key}"
            
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers=headers
            )
        return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make an HTTP request to AwesomeMiner API."""
        client = await self._get_client()
        
        try:
            response = await client.request(
                method=method,
                url=endpoint,
                params=params,
                json=json_data
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "AwesomeMiner API HTTP error",
                status_code=e.response.status_code,
                endpoint=endpoint
            )
            raise AwesomeMinerError(f"HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            logger.error(
                "AwesomeMiner API request error",
                error=str(e),
                endpoint=endpoint
            )
            raise AwesomeMinerError(f"Request failed: {str(e)}")
    
    # =========================================================================
    # Miner Information Endpoints
    # =========================================================================
    
    async def get_miners(self) -> List[MinerInfo]:
        """
        Get list of all miners with their current status.
        
        Returns:
            List of MinerInfo objects with current state
        """
        try:
            # AwesomeMiner API endpoint for miners list
            data = await self._request("GET", "/api/miners")
            
            miners = []
            # Handle different response formats
            if isinstance(data, list):
                miner_list = data
            elif isinstance(data, dict) and "miners" in data:
                miner_list = data["miners"]
            else:
                miner_list = []
            
            for miner_data in miner_list:
                try:
                    miners.append(MinerInfo(**miner_data))
                except Exception as e:
                    logger.warning(
                        "Failed to parse miner data",
                        error=str(e),
                        data=miner_data
                    )
            
            return miners
            
        except Exception as e:
            logger.error("Failed to get miners list", error=str(e))
            raise
    
    async def get_miner(self, miner_id: int) -> Optional[MinerInfo]:
        """
        Get detailed information for a specific miner.
        
        Args:
            miner_id: The miner's ID in AwesomeMiner
            
        Returns:
            MinerInfo object or None if not found
        """
        try:
            data = await self._request("GET", f"/api/miners/{miner_id}")
            return MinerInfo(**data)
        except AwesomeMinerError:
            return None
    
    # =========================================================================
    # Miner Control Endpoints
    # =========================================================================
    
    async def start_miner(self, miner_id: int) -> bool:
        """
        Start mining on a specific miner.
        
        Args:
            miner_id: The miner's ID
            
        Returns:
            True if command was successful
        """
        try:
            await self._request("POST", f"/api/miners/{miner_id}/start")
            logger.info("Miner started", miner_id=miner_id)
            return True
        except AwesomeMinerError as e:
            logger.error("Failed to start miner", miner_id=miner_id, error=str(e))
            return False
    
    async def stop_miner(self, miner_id: int) -> bool:
        """
        Stop mining on a specific miner.
        
        Args:
            miner_id: The miner's ID
            
        Returns:
            True if command was successful
        """
        try:
            await self._request("POST", f"/api/miners/{miner_id}/stop")
            logger.info("Miner stopped", miner_id=miner_id)
            return True
        except AwesomeMinerError as e:
            logger.error("Failed to stop miner", miner_id=miner_id, error=str(e))
            return False
    
    async def restart_miner(self, miner_id: int) -> bool:
        """
        Restart a specific miner.
        
        Args:
            miner_id: The miner's ID
            
        Returns:
            True if command was successful
        """
        try:
            await self._request("POST", f"/api/miners/{miner_id}/restart")
            logger.info("Miner restarted", miner_id=miner_id)
            return True
        except AwesomeMinerError as e:
            logger.error("Failed to restart miner", miner_id=miner_id, error=str(e))
            return False
    
    async def enable_miner(self, miner_id: int) -> bool:
        """
        Enable a miner (allow it to mine).
        
        Args:
            miner_id: The miner's ID
            
        Returns:
            True if command was successful
        """
        try:
            await self._request("POST", f"/api/miners/{miner_id}/enable")
            logger.info("Miner enabled", miner_id=miner_id)
            return True
        except AwesomeMinerError as e:
            logger.error("Failed to enable miner", miner_id=miner_id, error=str(e))
            return False
    
    async def disable_miner(self, miner_id: int) -> bool:
        """
        Disable a miner (prevent it from mining).
        
        Args:
            miner_id: The miner's ID
            
        Returns:
            True if command was successful
        """
        try:
            await self._request("POST", f"/api/miners/{miner_id}/disable")
            logger.info("Miner disabled", miner_id=miner_id)
            return True
        except AwesomeMinerError as e:
            logger.error("Failed to disable miner", miner_id=miner_id, error=str(e))
            return False
    
    # =========================================================================
    # Batch Operations
    # =========================================================================
    
    async def start_all_miners(self, miner_ids: Optional[List[int]] = None) -> Dict[int, bool]:
        """
        Start multiple miners concurrently.
        
        Args:
            miner_ids: List of miner IDs to start. If None, starts all miners.
            
        Returns:
            Dict mapping miner_id to success status
        """
        if miner_ids is None:
            miners = await self.get_miners()
            miner_ids = [m.id for m in miners if m.is_available]
        
        results = {}
        tasks = [self.start_miner(mid) for mid in miner_ids]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        
        for miner_id, outcome in zip(miner_ids, outcomes):
            if isinstance(outcome, Exception):
                results[miner_id] = False
            else:
                results[miner_id] = outcome
        
        return results
    
    async def stop_all_miners(self, miner_ids: Optional[List[int]] = None) -> Dict[int, bool]:
        """
        Stop multiple miners concurrently.
        
        Args:
            miner_ids: List of miner IDs to stop. If None, stops all miners.
            
        Returns:
            Dict mapping miner_id to success status
        """
        if miner_ids is None:
            miners = await self.get_miners()
            miner_ids = [m.id for m in miners]
        
        results = {}
        tasks = [self.stop_miner(mid) for mid in miner_ids]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        
        for miner_id, outcome in zip(miner_ids, outcomes):
            if isinstance(outcome, Exception):
                results[miner_id] = False
            else:
                results[miner_id] = outcome
        
        return results
    
    # =========================================================================
    # Power Control (if supported by miner firmware)
    # =========================================================================
    
    async def set_miner_power_limit(
        self,
        miner_id: int,
        power_limit_watts: int
    ) -> bool:
        """
        Set power limit for a miner (if supported).
        
        Note: This depends on the miner hardware/firmware supporting power limits.
        Many ASIC miners don't support this directly.
        
        Args:
            miner_id: The miner's ID
            power_limit_watts: Power limit in watts
            
        Returns:
            True if command was successful
        """
        try:
            await self._request(
                "POST",
                f"/api/miners/{miner_id}/powerlimit",
                json_data={"powerLimit": power_limit_watts}
            )
            logger.info(
                "Miner power limit set",
                miner_id=miner_id,
                power_limit=power_limit_watts
            )
            return True
        except AwesomeMinerError as e:
            logger.error(
                "Failed to set miner power limit",
                miner_id=miner_id,
                error=str(e)
            )
            return False
    
    # =========================================================================
    # Health Check
    # =========================================================================
    
    async def health_check(self) -> bool:
        """
        Check if AwesomeMiner server is reachable.
        
        Returns:
            True if server is healthy
        """
        try:
            await self._request("GET", "/api/miners")
            return True
        except Exception:
            return False


# Singleton instance
_client_instance: Optional[AwesomeMinerClient] = None


def get_awesome_miner_client() -> AwesomeMinerClient:
    """Get the singleton AwesomeMiner client instance."""
    global _client_instance
    if _client_instance is None:
        _client_instance = AwesomeMinerClient()
    return _client_instance
