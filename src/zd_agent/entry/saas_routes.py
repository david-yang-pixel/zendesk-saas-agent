"""Compatibility wrapper for SaaS route registration."""
from zd_agent.services.saas_routes import handle_healthz, register_saas_routes

__all__ = ["handle_healthz", "register_saas_routes"]
