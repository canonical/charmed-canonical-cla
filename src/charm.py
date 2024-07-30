#!/usr/bin/env python3

import logging
from typing import Dict, Optional, cast

import ops
from charms.data_platform_libs.v0.data_interfaces import (DatabaseCreatedEvent,
                                                          DatabaseRequires)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

SERVICE_PORT = 8000


class FastAPICharm(ops.CharmBase):
    """Charm the service."""
    app_container: ops.Container

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        # Provide ability for prometheus to be scraped by Prometheus using prometheus_scrape
        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[
                {"static_configs": [{"targets": [f"*:{SERVICE_PORT}"]}]}],
            refresh_event=self.on.config_changed,
        )

        # Enable log forwarding for Loki and other charms that implement loki_push_api
        self._logging = LokiPushApiConsumer(
            self, relation_name="log-proxy")

        # Provide grafana dashboards over a relation interface
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard")

        # Charm events defined in the database requires charm library.
        self.database = DatabaseRequires(
            self, relation_name="database", database_name="names_db")

        self.framework.observe(
            self.database.on.database_created, self._on_database_created)
        self.framework.observe(
            self.database.on.endpoints_changed, self._on_database_created)

        # Define the charm events
        self.container = self.unit.get_container("app")

        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.app_pebble_ready,
                          self._update_layer_and_restart)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)

    def _on_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event is fired when postgres database is created."""
        self._update_layer_and_restart(None)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        self._update_layer_and_restart()

    def _on_collect_status(self, event):
        # If nothing is wrong, then the status is active.
        event.add_status(ops.ActiveStatus())

        if not self.model.get_relation("database"):
            # We need the user to do 'juju integrate'.
            event.add_status(ops.BlockedStatus(
                "Waiting for database relation"))
        elif not self.database.fetch_relation_data():
            # We need the charms to finish integrating.
            event.add_status(ops.WaitingStatus(
                "Waiting for database relation"))
        try:
            status = self.container.get_service("app")
        except (ops.pebble.APIError, ops.ModelError, ops.pebble.ConnectionError):
            event.add_status(ops.MaintenanceStatus(
                "Waiting for Pebble in workload container"))
        else:
            if not status.is_running():
                event.add_status(ops.MaintenanceStatus(
                    "Waiting for the service to start up"))

    def _update_layer_and_restart(self, event=None) -> None:
        """Define and start a workload using the Pebble API.

        You'll need to specify the right entrypoint and environment
        configuration for your specific workload. Tip: you can see the
        standard entrypoint of an existing container using docker inspect

        Learn more about Pebble layers at https://github.com/canonical/pebble
        """

        new_layer_services = self._pebble_layer.to_dict().get("services", {})
        try:
            # Get the current pebble layer config
            services = self.container.get_plan().to_dict().get("services", {})
            if services != new_layer_services:
                # Changes were made, add the new layer
                self.container.add_layer(
                    "app", self._pebble_layer, combine=True)
                logger.info(
                    f"Added updated layer 'app' to Pebble plan")
                if event and isinstance(event, ops.PebbleReadyEvent):
                    self.container.replan()
                else:
                    self.container.restart('app')
                    logger.info(f"Restarted 'app' service")

        except (ops.pebble.ConnectionError, ops.pebble.APIError):
            logger.debug("Error updating Pebble layer", exc_info=True)

    @ property
    def _pebble_layer(self):
        """Return a dictionary representing a Pebble layer."""
        health_check_endpoint: ops.pebble.HttpDict = {
            "url": f"http://localhost:{SERVICE_PORT}/healthz"
        }
        pebble_layer: ops.pebble.LayerDict = {
            "services": {
                "app": {
                    "override": "replace",
                    "startup": "enabled",
                    "working-dir": "app",
                    "command": f"python3 -m fastapi run --host=0.0.0.0 --port={SERVICE_PORT} src/main.py",
                    "environment": self.app_environment,
                    "on-check-failure": {
                        # restart on checks.up failure
                        "up": 'restart'
                    },
                }
            },
            "checks": {
                "test": {
                    "override": "replace",
                    "http": health_check_endpoint
                },
                "online": {
                    "override": "replace",
                    "level": ops.pebble.CheckLevel.READY,
                    "http": health_check_endpoint
                },
                "up": {
                    "override": "replace",
                    "level": ops.pebble.CheckLevel.ALIVE,
                    "http": health_check_endpoint
                }
            }
        }
        return ops.pebble.Layer(pebble_layer)

    @ property
    def app_environment(self):
        """This property method creates a dictionary containing environment variables
        for the application. It retrieves the database authentication data by calling
        the `fetch_postgres_relation_data` method and uses it to populate the dictionary.
        If any of the values are not present, it will be set to None.
        The method returns this dictionary as output.
        """
        db_data = self.fetch_postgres_relation_data()
        if not db_data:
            return {}
        env = {
            "DB_HOST": db_data.get("db_host", None),
            "DB_PORT": db_data.get("db_port", None),
            "DB_USER": db_data.get("db_username", None),
            "DB_PASSWORD": db_data.get("db_password", None),
        }
        return env

    @ property
    def _pebble_log_targets(self) -> Dict[str, ops.pebble.LogTargetDict]:
        """Return a dictionary representing a Pebble log target.

        [Pebble docs](https://github.com/canonical/pebble?tab=readme-ov-file#log-forwarding)."""
        loki_push_api = cast(
            Optional[str], next(iter(self._logging.loki_endpoints), {}).get("url", None))

        if not loki_push_api:
            logger.error("Loki push api not available:",
                         self._logging.loki_endpoints)
            return {}
        return {
            "logs": {
                "override": "merge",
                "type": "loki",
                "location": loki_push_api,
                "services": ["app"]
            }
        }

    def fetch_postgres_relation_data(self):
        """Fetch postgres relation data.

        This function retrieves relation data from a postgres database using
        the `fetch_relation_data` method of the `database` object. The retrieved data is
        then logged for debugging purposes, and any non-empty data is processed to extract
        endpoint information, username, and password. This processed data is then returned as
        a dictionary. If no data is retrieved, the unit is set to waiting status and
        the program exits with a zero status code."""
        relations = self.database.fetch_relation_data()
        logger.debug("Got following database data: %s", relations)
        for data in relations.values():
            if not data:
                continue
            logger.info("New PSQL database endpoint is %s", data["endpoints"])
            host, port = data["endpoints"].split(":")
            db_data = {
                "db_host": host,
                "db_port": port,
                "db_username": data["username"],
                "db_password": data["password"],
            }
            return db_data
        return {}


if __name__ == "__main__":  # pragma: nocover
    ops.main(FastAPICharm)  # type: ignore
