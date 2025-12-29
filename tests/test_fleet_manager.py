"""
Tests for FleetManager service.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.services.fleet_manager import FleetManager
from app.models.ems import RunningStatus
from app.models.state import FleetState, MinerState
from app.models.miner import MinerInfo


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.poll_interval_seconds = 5
    settings.min_power_threshold_kw = 1.0
    settings.rated_power_kw = None
    return settings


@pytest.fixture
def mock_am_client():
    """Create mock AwesomeMiner client."""
    client = AsyncMock()
    return client


@pytest.fixture
def fleet_manager(mock_settings, mock_am_client):
    """Create FleetManager with mocked dependencies."""
    return FleetManager(settings=mock_settings, am_client=mock_am_client)


class TestFleetManagerStatus:
    """Tests for fleet status management."""
    
    @pytest.mark.asyncio
    async def test_update_status_calculates_power(self, fleet_manager, mock_am_client):
        """Verify power calculations from miner data."""
        # Setup mock miners
        mock_am_client.get_miners = AsyncMock(return_value=[
            MinerInfo(id=1, name="Miner1", status="Mining", power_usage=3000),
            MinerInfo(id=2, name="Miner2", status="Mining", power_usage=2500),
            MinerInfo(id=3, name="Miner3", status="Stopped", power_usage=0),
        ])
        
        await fleet_manager.update_status()
        
        assert fleet_manager.status.active_power_kw == 5.5  # 5500W = 5.5kW
    
    @pytest.mark.asyncio
    async def test_update_status_counts_miners(self, fleet_manager, mock_am_client):
        """Verify miner counting."""
        mock_am_client.get_miners = AsyncMock(return_value=[
            MinerInfo(id=1, name="M1", status="Mining"),
            MinerInfo(id=2, name="M2", status="Mining"),
            MinerInfo(id=3, name="M3", status="Stopped"),
            MinerInfo(id=4, name="M4", status="Offline"),
        ])
        
        await fleet_manager.update_status()
        
        assert fleet_manager.status.total_miners == 4
        assert fleet_manager.status.online_miners == 3  # Offline excluded
        assert fleet_manager.status.mining_miners == 2


class TestFleetManagerActivation:
    """Tests for activation logic."""
    
    @pytest.mark.asyncio
    async def test_activate_starts_miners(self, fleet_manager, mock_am_client):
        """Verify activation starts appropriate miners."""
        # Setup initial status
        fleet_manager._status.is_available_for_dispatch = True
        fleet_manager._status.rated_power_kw = 100.0
        fleet_manager._status.miners = [
            MinerState(miner_id=1, name="M1", is_online=True, rated_power_kw=30.0),
            MinerState(miner_id=2, name="M2", is_online=True, rated_power_kw=30.0),
            MinerState(miner_id=3, name="M3", is_online=True, rated_power_kw=30.0),
        ]
        
        mock_am_client.start_all_miners = AsyncMock(return_value={1: True, 2: True})
        
        success, message = await fleet_manager.activate(50.0)
        
        assert success is True
        mock_am_client.start_all_miners.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_activate_rejects_excess_power(self, fleet_manager):
        """Verify activation rejects power exceeding rated."""
        fleet_manager._status.is_available_for_dispatch = True
        fleet_manager._status.rated_power_kw = 100.0
        
        success, message = await fleet_manager.activate(150.0)
        
        assert success is False
        assert "exceeds" in message.lower()
    
    @pytest.mark.asyncio
    async def test_activate_when_unavailable(self, fleet_manager):
        """Verify activation fails when not available."""
        fleet_manager._status.is_available_for_dispatch = False
        fleet_manager._status.rated_power_kw = 100.0
        
        success, message = await fleet_manager.activate(50.0)
        
        assert success is False


class TestFleetManagerDeactivation:
    """Tests for deactivation logic."""
    
    @pytest.mark.asyncio
    async def test_deactivate_stops_miners(self, fleet_manager, mock_am_client):
        """Verify deactivation stops all mining miners."""
        fleet_manager._status.miners = [
            MinerState(miner_id=1, name="M1", is_mining=True),
            MinerState(miner_id=2, name="M2", is_mining=True),
            MinerState(miner_id=3, name="M3", is_mining=False),
        ]
        
        mock_am_client.stop_all_miners = AsyncMock(return_value={1: True, 2: True})
        
        success, message = await fleet_manager.deactivate()
        
        assert success is True
        mock_am_client.stop_all_miners.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_deactivate_idempotent(self, fleet_manager):
        """Verify deactivation succeeds when already in standby."""
        fleet_manager._status.miners = []  # No mining miners
        fleet_manager._status.state = FleetState.STANDBY
        
        success, message = await fleet_manager.deactivate()
        
        assert success is True


class TestFleetManagerOverride:
    """Tests for manual override functionality."""
    
    @pytest.mark.asyncio
    async def test_enable_override(self, fleet_manager, mock_am_client):
        """Verify override mode can be enabled."""
        fleet_manager._status.miners = [
            MinerState(miner_id=1, name="M1", is_online=True, rated_power_kw=30.0),
        ]
        mock_am_client.start_all_miners = AsyncMock(return_value={1: True})
        
        success, _ = await fleet_manager.set_manual_override(True, 25.0)
        
        assert success is True
        assert fleet_manager._manual_override is True
        assert fleet_manager._override_power_kw == 25.0
    
    @pytest.mark.asyncio
    async def test_disable_override(self, fleet_manager):
        """Verify override mode can be disabled."""
        fleet_manager._manual_override = True
        fleet_manager._override_power_kw = 50.0
        
        success, _ = await fleet_manager.set_manual_override(False)
        
        assert success is True
        assert fleet_manager._manual_override is False
        assert fleet_manager._override_power_kw is None
