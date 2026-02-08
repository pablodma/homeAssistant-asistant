"""Health check tests."""

import pytest
from fastapi.testclient import TestClient


def test_health_check():
    """Test health check endpoint returns healthy status."""
    # Import here to avoid issues with missing env vars
    from src.app.main import app
    
    client = TestClient(app)
    response = client.get("/health")
    
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root_endpoint():
    """Test root endpoint returns service info."""
    from src.app.main import app
    
    client = TestClient(app)
    response = client.get("/")
    
    assert response.status_code == 200
    assert "service" in response.json()
    assert response.json()["status"] == "running"
