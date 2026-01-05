"""
Database module for persisting dashboard and miner data.

Uses SQLite for simple file-based persistence.
"""
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, DateTime, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import structlog

logger = structlog.get_logger()

# Database setup
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'net_stabilization.db')}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# =========================================================================
# Models
# =========================================================================

class MinerRecord(Base):
    """Persistent miner configuration and metadata."""
    __tablename__ = "miners"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(45), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=True)
    model = Column(String(100), nullable=True)
    firmware = Column(String(50), nullable=True)
    firmware_version = Column(String(20), nullable=True)
    mac_address = Column(String(17), nullable=True)
    serial_number = Column(String(50), nullable=True)
    rated_power_watts = Column(Integer, default=1400)
    pool_url = Column(String(255), nullable=True)
    pool_worker = Column(String(100), nullable=True)
    pool_password = Column(String(100), nullable=True)
    enabled = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_seen = Column(DateTime, nullable=True)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ip": self.ip,
            "name": self.name,
            "model": self.model,
            "firmware": self.firmware,
            "firmware_version": self.firmware_version,
            "mac_address": self.mac_address,
            "serial_number": self.serial_number,
            "rated_power_watts": self.rated_power_watts,
            "pool_url": self.pool_url,
            "pool_worker": self.pool_worker,
            "enabled": self.enabled,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


class MinerSnapshot(Base):
    """Historical miner data snapshots for charts and analysis."""
    __tablename__ = "miner_snapshots"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    miner_ip = Column(String(45), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    hashrate_ghs = Column(Float, nullable=True)
    power_watts = Column(Float, nullable=True)
    temperature = Column(Float, nullable=True)
    fan_speed = Column(Integer, nullable=True)
    frequency = Column(Integer, nullable=True)
    voltage = Column(Float, nullable=True)
    is_mining = Column(Boolean, default=False)
    accepted_shares = Column(Integer, nullable=True)
    rejected_shares = Column(Integer, nullable=True)
    hardware_errors = Column(Integer, nullable=True)
    uptime_seconds = Column(Integer, nullable=True)


class FleetSnapshot(Base):
    """Aggregated fleet-level snapshots."""
    __tablename__ = "fleet_snapshots"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    total_hashrate_ghs = Column(Float, nullable=True)
    total_power_watts = Column(Float, nullable=True)
    avg_temperature = Column(Float, nullable=True)
    miners_online = Column(Integer, default=0)
    miners_mining = Column(Integer, default=0)
    miners_total = Column(Integer, default=0)
    fleet_state = Column(String(20), nullable=True)


class CommandHistory(Base):
    """Log of commands sent to fleet/miners."""
    __tablename__ = "command_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    command_type = Column(String(50), nullable=False)  # activate, deactivate, set_power, etc.
    source = Column(String(20), nullable=False)  # ems, dashboard, api
    target = Column(String(50), nullable=True)  # fleet, miner_ip
    parameters = Column(JSON, nullable=True)
    success = Column(Boolean, default=True)
    message = Column(Text, nullable=True)


class DashboardSettings(Base):
    """Persistent dashboard/system settings."""
    __tablename__ = "dashboard_settings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(50), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    value_type = Column(String(20), default="string")  # string, int, float, bool, json
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# =========================================================================
# Database Operations
# =========================================================================

def init_db():
    """Initialize the database and create tables."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized", path=DATABASE_URL)


@contextmanager
def get_db() -> Session:
    """Get a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DatabaseService:
    """Service for database operations."""
    
    def __init__(self):
        init_db()
        self._ensure_default_settings()
    
    def _ensure_default_settings(self):
        """Ensure default settings exist."""
        defaults = [
            ("power_control_mode", "on_off", "string", "Power control mode: on_off or frequency"),
            ("manual_override", "false", "bool", "Manual override enabled"),
            ("override_power_kw", "", "float", "Override target power in kW"),
        ]
        with get_db() as db:
            for key, value, value_type, description in defaults:
                existing = db.query(DashboardSettings).filter_by(key=key).first()
                if not existing:
                    db.add(DashboardSettings(
                        key=key,
                        value=value,
                        value_type=value_type,
                        description=description
                    ))
            db.commit()
    
    # =========================================================================
    # Settings Operations
    # =========================================================================
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value."""
        with get_db() as db:
            setting = db.query(DashboardSettings).filter_by(key=key).first()
            if not setting:
                return default
            
            # Convert value based on type
            if setting.value_type == "int":
                return int(setting.value) if setting.value else default
            elif setting.value_type == "float":
                return float(setting.value) if setting.value else default
            elif setting.value_type == "bool":
                return setting.value.lower() in ("true", "1", "yes") if setting.value else default
            elif setting.value_type == "json":
                import json
                return json.loads(setting.value) if setting.value else default
            else:
                return setting.value if setting.value else default
    
    def set_setting(self, key: str, value: Any, value_type: str = None):
        """Set a setting value."""
        with get_db() as db:
            setting = db.query(DashboardSettings).filter_by(key=key).first()
            
            # Convert value to string
            if isinstance(value, bool):
                str_value = "true" if value else "false"
                value_type = value_type or "bool"
            elif isinstance(value, (dict, list)):
                import json
                str_value = json.dumps(value)
                value_type = value_type or "json"
            else:
                str_value = str(value) if value is not None else ""
                if value_type is None:
                    if isinstance(value, int):
                        value_type = "int"
                    elif isinstance(value, float):
                        value_type = "float"
                    else:
                        value_type = "string"
            
            if setting:
                setting.value = str_value
                if value_type:
                    setting.value_type = value_type
            else:
                db.add(DashboardSettings(key=key, value=str_value, value_type=value_type))
            db.commit()
    
    # =========================================================================
    # Miner Operations
    # =========================================================================
    
    def get_miner(self, ip: str) -> Optional[MinerRecord]:
        """Get miner by IP."""
        with get_db() as db:
            return db.query(MinerRecord).filter_by(ip=ip).first()
    
    def get_all_miners(self) -> List[MinerRecord]:
        """Get all miners."""
        with get_db() as db:
            return db.query(MinerRecord).all()
    
    def upsert_miner(self, ip: str, **kwargs) -> MinerRecord:
        """Create or update a miner record."""
        with get_db() as db:
            miner = db.query(MinerRecord).filter_by(ip=ip).first()
            if miner:
                for key, value in kwargs.items():
                    if hasattr(miner, key) and value is not None:
                        setattr(miner, key, value)
                miner.last_seen = datetime.utcnow()
            else:
                miner = MinerRecord(ip=ip, **kwargs)
                miner.last_seen = datetime.utcnow()
                db.add(miner)
            db.commit()
            db.refresh(miner)
            return miner
    
    def update_miner_last_seen(self, ip: str):
        """Update miner's last_seen timestamp."""
        with get_db() as db:
            miner = db.query(MinerRecord).filter_by(ip=ip).first()
            if miner:
                miner.last_seen = datetime.utcnow()
                db.commit()
    
    def delete_miner(self, ip: str) -> bool:
        """Delete a miner record."""
        with get_db() as db:
            miner = db.query(MinerRecord).filter_by(ip=ip).first()
            if miner:
                db.delete(miner)
                db.commit()
                return True
            return False
    
    # =========================================================================
    # Snapshot Operations
    # =========================================================================
    
    def save_miner_snapshot(self, miner_ip: str, **data):
        """Save a miner snapshot."""
        with get_db() as db:
            snapshot = MinerSnapshot(miner_ip=miner_ip, **data)
            db.add(snapshot)
            db.commit()
    
    def save_fleet_snapshot(self, **data):
        """Save a fleet snapshot."""
        with get_db() as db:
            snapshot = FleetSnapshot(**data)
            db.add(snapshot)
            db.commit()
    
    def get_miner_snapshots(
        self, 
        miner_ip: str, 
        hours: int = 24, 
        limit: int = 1000
    ) -> List[MinerSnapshot]:
        """Get recent miner snapshots."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        with get_db() as db:
            return db.query(MinerSnapshot).filter(
                MinerSnapshot.miner_ip == miner_ip,
                MinerSnapshot.timestamp >= cutoff
            ).order_by(MinerSnapshot.timestamp.desc()).limit(limit).all()
    
    def get_fleet_snapshots(self, hours: int = 24, limit: int = 1000) -> List[FleetSnapshot]:
        """Get recent fleet snapshots."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        with get_db() as db:
            return db.query(FleetSnapshot).filter(
                FleetSnapshot.timestamp >= cutoff
            ).order_by(FleetSnapshot.timestamp.desc()).limit(limit).all()
    
    def cleanup_old_snapshots(self, days: int = 7):
        """Remove snapshots older than specified days."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        with get_db() as db:
            db.query(MinerSnapshot).filter(MinerSnapshot.timestamp < cutoff).delete()
            db.query(FleetSnapshot).filter(FleetSnapshot.timestamp < cutoff).delete()
            db.commit()
            logger.info("Cleaned up old snapshots", older_than_days=days)
    
    # =========================================================================
    # Command History Operations
    # =========================================================================
    
    def log_command(
        self,
        command_type: str,
        source: str,
        target: str = None,
        parameters: Dict = None,
        success: bool = True,
        message: str = None
    ):
        """Log a command to history."""
        with get_db() as db:
            cmd = CommandHistory(
                command_type=command_type,
                source=source,
                target=target,
                parameters=parameters,
                success=success,
                message=message
            )
            db.add(cmd)
            db.commit()
    
    def get_command_history(self, limit: int = 50) -> List[CommandHistory]:
        """Get recent command history."""
        with get_db() as db:
            return db.query(CommandHistory).order_by(
                CommandHistory.timestamp.desc()
            ).limit(limit).all()
    
    # =========================================================================
    # Data Cleanup / Retention
    # =========================================================================
    
    def cleanup_old_snapshots(self, retention_hours: int = 24) -> Dict[str, int]:
        """
        Delete snapshots older than retention period.
        
        Args:
            retention_hours: Hours to keep data (default 24)
            
        Returns:
            Dict with count of deleted rows per table
        """
        cutoff = datetime.utcnow() - timedelta(hours=retention_hours)
        deleted = {}
        
        with get_db() as db:
            # Clean miner snapshots
            result = db.query(MinerSnapshot).filter(
                MinerSnapshot.timestamp < cutoff
            ).delete(synchronize_session=False)
            deleted["miner_snapshots"] = result
            
            # Clean fleet snapshots
            result = db.query(FleetSnapshot).filter(
                FleetSnapshot.timestamp < cutoff
            ).delete(synchronize_session=False)
            deleted["fleet_snapshots"] = result
            
            # Clean old command history (keep 7 days)
            cmd_cutoff = datetime.utcnow() - timedelta(days=7)
            result = db.query(CommandHistory).filter(
                CommandHistory.timestamp < cmd_cutoff
            ).delete(synchronize_session=False)
            deleted["command_history"] = result
            
            db.commit()
        
        return deleted
    
    def get_snapshot_counts(self) -> Dict[str, int]:
        """Get current row counts for snapshot tables."""
        with get_db() as db:
            return {
                "miner_snapshots": db.query(MinerSnapshot).count(),
                "fleet_snapshots": db.query(FleetSnapshot).count(),
                "command_history": db.query(CommandHistory).count(),
            }


# Singleton instance
_db_service: Optional[DatabaseService] = None


def get_db_service() -> DatabaseService:
    """Get the database service singleton."""
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
    return _db_service
