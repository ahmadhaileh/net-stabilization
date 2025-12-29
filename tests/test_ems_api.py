"""
Tests for EMS API endpoints.

These tests verify compliance with the EMS protocol specification.
"""
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

from app.main import app
from app.models.ems import RunningStatus
from app.models.state import FleetState, FleetStatus


@pytest.fixture
def mock_fleet_status():
    """Create a mock fleet status."""
    return FleetStatus(
        state=FleetState.RUNNING,
        is_available_for_dispatch=True,
        running_status=RunningStatus.RUNNING,
        rated_power_kw=500.0,
        active_power_kw=250.0,
        total_miners=10,
        online_miners=8,
        mining_miners=5
    )


@pytest.fixture
def mock_fleet_manager(mock_fleet_status):
    """Create a mock fleet manager."""
    manager = AsyncMock()
    manager.status = mock_fleet_status
    manager.activate = AsyncMock(return_value=(True, "Fleet activated successfully."))
    manager.deactivate = AsyncMock(return_value=(True, "Fleet deactivation command accepted."))
    return manager


class TestEMSStatusEndpoint:
    """Tests for GET /api/status endpoint."""
    
    @pytest.mark.asyncio
    async def test_status_returns_correct_format(self, mock_fleet_manager):
        """Verify status response matches EMS spec."""
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.get("/api/status")
        
        assert response.status_code == 200
        data = response.json()
        
        # Verify required fields
        assert "isAvailableForDispatch" in data
        assert "runningStatus" in data
        assert "ratedPowerInKw" in data
        assert "activePowerInKw" in data
        
        # Verify types
        assert isinstance(data["isAvailableForDispatch"], bool)
        assert isinstance(data["runningStatus"], int)
        assert isinstance(data["ratedPowerInKw"], (int, float))
        assert isinstance(data["activePowerInKw"], (int, float))
    
    @pytest.mark.asyncio
    async def test_status_running_status_values(self, mock_fleet_manager):
        """Verify runningStatus uses correct enum values."""
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.get("/api/status")
        
        data = response.json()
        assert data["runningStatus"] in [1, 2]  # 1=StandBy, 2=Running


class TestEMSActivateEndpoint:
    """Tests for POST /api/activate endpoint."""
    
    @pytest.mark.asyncio
    async def test_activate_success(self, mock_fleet_manager):
        """Verify successful activation."""
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/activate",
                    json={"activationPowerInKw": 100.0}
                )
        
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] is True
        assert "message" in data
    
    @pytest.mark.asyncio
    async def test_activate_exceeds_rated_power(self, mock_fleet_manager):
        """Verify 400 error when power exceeds rated."""
        mock_fleet_manager.activate = AsyncMock(
            return_value=(False, "Requested power exceeds rated limits.")
        )
        
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/activate",
                    json={"activationPowerInKw": 1000.0}
                )
        
        assert response.status_code == 400
        data = response.json()
        assert data["accepted"] is False
    
    @pytest.mark.asyncio
    async def test_activate_with_override_active(self, mock_fleet_manager):
        """Verify 409 error when manual override is active."""
        mock_fleet_manager.status.manual_override_active = True
        
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/activate",
                    json={"activationPowerInKw": 100.0}
                )
        
        assert response.status_code == 409
        data = response.json()
        assert data["accepted"] is False


class TestEMSDeactivateEndpoint:
    """Tests for POST /api/deactivate endpoint."""
    
    @pytest.mark.asyncio
    async def test_deactivate_success(self, mock_fleet_manager):
        """Verify successful deactivation."""
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/deactivate",
                    json={}
                )
        
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] is True
    
    @pytest.mark.asyncio
    async def test_deactivate_idempotent(self, mock_fleet_manager):
        """Verify deactivation is idempotent (success when already standby)."""
        mock_fleet_manager.status.state = FleetState.STANDBY
        mock_fleet_manager.status.running_status = RunningStatus.STANDBY
        
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/deactivate",
                    json={}
                )
        
        assert response.status_code == 200
        data = response.json()
        assert data["accepted"] is True


class TestEMSErrorResponses:
    """Tests for error response format compliance."""
    
    @pytest.mark.asyncio
    async def test_error_response_format(self, mock_fleet_manager):
        """Verify all error responses match EMS spec format."""
        mock_fleet_manager.activate = AsyncMock(
            return_value=(False, "Test error message")
        )
        
        with patch('app.api.ems.get_fleet_manager', return_value=mock_fleet_manager):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post(
                    "/api/activate",
                    json={"activationPowerInKw": 100.0}
                )
        
        data = response.json()
        
        # Per spec: errors must have 'accepted' and 'message'
        assert "accepted" in data
        assert "message" in data
        assert data["accepted"] is False
        assert isinstance(data["message"], str)
